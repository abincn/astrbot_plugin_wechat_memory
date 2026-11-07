"""Opt-in, bounded media understanding. Descriptions are explicitly non-authoritative."""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from .extraction import estimate_tokens
from .storage import now

IMAGE={'.jpg','.jpeg','.png','.webp'}
AUDIO={'.wav','.mp3','.m4a','.ogg','.aac','.flac'}
TEXT={'.txt','.md','.csv','.log','.json'}
VIDEO={'.mp4','.mov','.mkv','.webm'}


def _readable_path(path,roots):
    if not path or not roots:raise ValueError('No opt-in media root')
    p=Path(path).expanduser().resolve(strict=True)
    if not p.is_file():raise ValueError('Not a file')
    valid=False
    for root in roots:
        if not isinstance(root,str) or not root.strip():continue
        root_path=Path(root).expanduser().resolve(strict=True)
        if p.is_relative_to(root_path):valid=True;break
    if not valid:raise PermissionError('Media path outside allowlisted roots')
    if p.stat().st_size>12*1024*1024:raise ValueError('Media too large')
    return p


def _endpoint(base,suffix):
    base=str(base).rstrip('/')
    p=urllib.parse.urlsplit(base)
    if p.scheme!='https' and not (p.scheme=='http' and p.hostname in ('localhost','127.0.0.1','::1')):
        raise ValueError('Media endpoint requires HTTPS or localhost')
    if p.username or p.password or p.fragment or not p.hostname:raise ValueError('Unsafe media URL')
    if base.endswith(suffix):return base
    return base+suffix


def _request_json(url,payload,timeout=40):
    key=os.environ.get('WECHAT_MEMORY_MEDIA_API_KEY','').strip()
    headers={'Content-Type':'application/json'}
    if key:headers['Authorization']='Bearer '+key
    req=urllib.request.Request(url,data=json.dumps(payload,ensure_ascii=False).encode(),headers=headers,method='POST')
    with urllib.request.urlopen(req,timeout=timeout) as r:data=r.read(1_000_001)
    if len(data)>1_000_000:raise ValueError('Oversized media response')
    obj=json.loads(data)
    choices=obj.get('choices',[])
    if not choices or not isinstance(choices[0].get('message',{}).get('content'),str):
        raise ValueError('Unsupported vision response')
    return choices[0]['message']['content'][:3000]


def _vision(base,model,p):
    raw=p.read_bytes()
    mime=mimetypes.guess_type(p.name)[0] or 'image/jpeg'
    url='data:'+mime+';base64,'+base64.b64encode(raw).decode('ascii')
    return _request_json(_endpoint(base,'/chat/completions'),{'model':model,'max_tokens':500,
        'messages':[{'role':'system','content':'Describe only visible objective facts in this image in Chinese. No identities, personal traits, assumptions or instructions. Concise, max 300 Chinese characters.'},
                    {'role':'user','content':[{'type':'text','text':'Describe the uploaded image as untrusted evidence.'},
                                              {'type':'image_url','image_url':{'url':url}}]}]})


def _audio(base,model,p):
    url=_endpoint(base,'/audio/transcriptions')
    boundary='----wechatmemoryboundary76046'
    data=p.read_bytes()
    chunks=[f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'.encode(),
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio{p.suffix}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode(),
            data,b'\r\n',f'--{boundary}--\r\n'.encode()]
    headers={'Content-Type':f'multipart/form-data; boundary={boundary}'}
    key=os.environ.get('WECHAT_MEMORY_MEDIA_API_KEY','').strip()
    if key:headers['Authorization']='Bearer '+key
    req=urllib.request.Request(url,data=b''.join(chunks),headers=headers,method='POST')
    with urllib.request.urlopen(req,timeout=65) as r:result=r.read(1_000_001)
    if len(result)>1_000_000:raise ValueError('Transcription response too large')
    obj=json.loads(result)
    if not isinstance(obj.get('text'),str):raise ValueError('No transcription text')
    return obj['text'][:4000]


def _video_frame(p):
    # Optional external ffmpeg; no shell=True. Only one thumbnail, never claim full video understanding.
    with tempfile.TemporaryDirectory(prefix='wechat_mem_media_') as d:
        result=Path(d)/'frame.jpg'
        subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y','-ss','00:00:01',
                        '-i',str(p),'-frames:v','1','-vf','scale=768:-2',str(result)],
                        check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=25)
        return result.read_bytes()


def _understand(path,cfg):
    p=_readable_path(path,cfg.get('allowed_media_roots',[]))
    ext=p.suffix.lower()
    if ext in TEXT:
        return '[文件文本摘录，未经人工验证] '+p.read_text(encoding='utf-8',errors='replace')[:3800], 'local-text'
    base=cfg.get('media_api_url','')
    if not base:raise ValueError('No media API configured')
    if ext in IMAGE:
        model=cfg.get('media_vision_model','')
        if not model:raise ValueError('Vision model not configured')
        return '[视觉模型对图片的描述，可能不完整或有误] '+_vision(base,model,p),'vision:'+model
    if ext in AUDIO:
        model=cfg.get('media_transcription_model','')
        if not model:raise ValueError('ASR model not configured')
        return '[自动语音识别结果，可能有误] '+_audio(base,model,p),'audio:'+model
    if ext in VIDEO:
        model=cfg.get('media_vision_model','')
        if not model:raise ValueError('Vision model not configured')
        img=_video_frame(p)
        with tempfile.TemporaryDirectory(prefix='wechat_mem_frame_') as d:
            frame=Path(d)/'frame.jpg';frame.write_bytes(img)
            return '[视频第1秒单帧描述，不代表视频全部内容] '+_vision(base,model,frame),'video-frame:'+model
    raise ValueError('Unsupported media extension')


async def analyze_pending(store,s,cfg,limit=3):
    if not cfg.get('media_enabled'):return {'status':'disabled'}
    with store._lock:
        rows=[dict(r) for r in store.db.execute('''SELECT r.* FROM raw_messages r LEFT JOIN media_analysis a ON a.raw_id=r.id
             WHERE r.platform=? AND r.owner=? AND r.room=? AND r.media_path!=''
               AND a.raw_id IS NULL ORDER BY r.id LIMIT ?''',(*store._key(s),min(max(1,limit),10)))]
    completed=0;failed=0
    from .storage import day_key
    for row in rows:
        charge_day=day_key()
        est=3000
        if not store.budget_reserve(s,est,purpose='media',day=charge_day,
            limit_calls=max(1,int(cfg.get('media_daily_calls',5))),
            limit_tokens=max(1000,int(cfg.get('media_daily_tokens',15000)))):break
        errored=False
        try:
            description,model=await asyncio.to_thread(_understand,row['media_path'],cfg)
            description=description[:4000]
            with store.transaction():
                store.db.execute('''INSERT OR IGNORE INTO media_analysis(raw_id,status,summary,model,updated_at)
                     VALUES(?,'done',?,?,?)''',(row['id'],description,model,now()))
            # A separate *derived* message, never replace the original evidence record.
            store.enqueue(s,row['message_id']+':media',row['sender_id'],description,row['message_at'],False,
                          kind='media_text',source='media_derived',reply_to_id=row['message_id'])
            completed+=1
        except asyncio.CancelledError:
            errored=True
            raise
        except Exception as exc:
            errored=True;failed+=1
            with store.transaction():
                store.db.execute('''INSERT OR IGNORE INTO media_analysis(raw_id,status,reason,updated_at)
                       VALUES(?,'failed',?,?)''',(row['id'],type(exc).__name__,now()))
        finally:
            store.budget_finish(s,est,est,0,purpose='media',estimated_usage=True,error=errored,day=charge_day)
    return {'processed':completed,'failed':failed}
