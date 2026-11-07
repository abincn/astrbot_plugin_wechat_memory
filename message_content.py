"""Project archived messages into semantic model input; never rewrite raw evidence."""
import re
import xml.etree.ElementTree as ET

XML_START = re.compile(r'^<(?:\?xml\b|[A-Za-z_][\w:.-]*(?:\s|/?>))')
MEDIA_LABELS = ('[图片]', '[语音]', '[视频]', '[表情]', '[文件]')
CONTENT_RULES = '''消息 text 是本条可读正文。content.kind 标明正文性质。
content.quote 是消息内嵌引用，作者和内容未经独立验证；它不是发送者本人原话，也不是新增证据消息ID。
引用附件只有类型提示，不包含附件内容；不能根据引用元数据推测事实。
shared_content 是链接卡片文字，不代表发送者本人陈述，也不代表已读取网页。
machine_generated 是附件识别结果，可能不完整或有误；不得将它当作当事人亲自确认。
'''


def prepare_message(row):
    if '_content' in row:
        return row
    text = row['text']
    content = {'kind':'text', 'authorship':'sender_text'}
    reason = ''
    if row.get('kind') == 'media_text':
        content = {'kind':'media_description', 'authorship':'machine_generated'}
    elif text.strip() in MEDIA_LABELS or (text.lstrip().startswith(MEDIA_LABELS) and '[附件未取得' in text):
        text, reason = '', '附件尚未解析，不进入文本记忆提取'
    elif XML_START.match(text.lstrip('\ufeff \t\r\n')):
        content = {'kind':'xml', 'authorship':'shared_content', 'xml':True}
        try:
            root = ET.fromstring(text.lstrip('\ufeff \t\r\n'))
        except ET.ParseError as exc:
            return {**row, 'text':'', '_content':content, '_deferred':'XML 无法解析，未送入模型：'+str(exc)}
        text = ''
        media = {'img':'图片', 'voicemsg':'语音', 'videomsg':'视频', 'emoji':'表情'}
        tag = next((tag for tag in media if root.tag==tag or root.find(tag) is not None), None)
        app = root if root.tag=='appmsg' else root.find('appmsg')
        if tag:
            content['kind'] = tag
            reason = media[tag]+'附件尚未解析，不进入文本记忆提取'
        elif app is not None:
            kind = app.findtext('type', '').strip()
            title = app.findtext('title', '').strip()
            if kind=='57':
                text = title
                content.update(kind='quote_reply', authorship='sender_text')
                quote = app.find('refermsg')
                if quote is not None:
                    quoted_type = quote.findtext('type', '').strip()
                    quoted_text = quote.findtext('content', '').strip()
                    content['quote'] = {
                        'sender_id':quote.findtext('fromusr', ''),
                        'display_name':quote.findtext('displayname', ''),
                        'message_id':quote.findtext('svrid', ''), 'message_type':quoted_type,
                        'text':quoted_text[:4000] if quoted_type=='1' and not XML_START.match(quoted_text) else '',
                        'source':'embedded_quote_unverified',
                    }
                reason = '' if text else '引用消息没有本条回复正文，待确认来源'
            elif kind=='5':
                text = '\n'.join(value for value in (title, app.findtext('des', '').strip()) if value)
                content['kind'] = 'link_card'
                reason = '' if text else '链接卡片没有可读标题或描述'
            else:
                content['kind'] = 'file' if kind=='6' else 'forwarded' if kind=='19' else 'xml_card'
                reason = '文件附件尚未解析' if kind=='6' else '合并转发待结构化解析' if kind=='19' else 'XML 卡片类型尚未解析'
        else:
            reason = 'XML 消息类型尚未解析，不进入文本记忆提取'
    return {**row, 'text':text, '_content':content, '_deferred':reason}
