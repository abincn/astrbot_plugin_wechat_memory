import asyncio
from astrbot.core.agent.tool import ToolSet


class Provider:
    def __init__(self, context):
        self.context = context

    async def generate(self, provider, system, prompt):
        async with asyncio.timeout(90):
            response = await self.context.llm_generate(chat_provider_id=provider, system_prompt=system,
                                                       prompt=prompt, tools=ToolSet())
        usage = getattr(response, 'usage', None)
        if isinstance(usage, dict):
            inp, out = usage.get('prompt_tokens', usage.get('input_tokens')), usage.get('completion_tokens', usage.get('output_tokens'))
        else:
            inp = getattr(usage, 'prompt_tokens', getattr(usage, 'input_tokens', None))
            out = getattr(usage, 'completion_tokens', getattr(usage, 'output_tokens', None))
        return {'text': response.completion_text, 'input': inp, 'output': out,
                'invalid_role': getattr(response, 'role', 'assistant')!='assistant' or bool(getattr(response, 'tools_call_name', None))}
