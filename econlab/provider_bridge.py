"""All EconLab inference uses the official, logged-in Codex app-server."""
from __future__ import annotations
import queue
import time
from codex_bridge import bridge as codex, agent_bridge as agent, Bridge

class ProviderBridge:
    def delegate(self):
        return codex

    @property
    def listeners(self):
        return codex.listeners

    def release(self,thread):
        for client in (codex,agent):
            client.listeners.pop(thread,None);client.tool_handlers.pop(thread,None)

    def status(self):
        result = codex.status()
        result.update(provider='codex', pdf_input_mode='all_pages_visual', native_pdf_attachment=False, ocr_enabled=False)
        return result

    def models(self):
        return codex.models()

    def select_model(self, saved=None):
        return None if saved and saved.lower().startswith('deepseek') else saved

    def begin(self, prompt, model=None, output_schema=None, page_images=None,thread_id=None,persistent=False,task=None):
        model=self.select_model(model)
        if not model:
            catalog=self.models()
            chosen=next((item for item in catalog if item.get('default')),catalog[0] if catalog else None)
            if not chosen:raise ValueError('Codex没有返回可用模型，请检查账户登录')
            model=chosen['model']
        client=agent if task else codex
        return client.begin(prompt, model, output_schema=output_schema, page_images=page_images,thread_id=thread_id,persistent=persistent,task=task)

    def interrupt(self, thread, turn):
        client=agent if thread in agent.listeners else codex
        return client.interrupt(thread, turn)

    def read_turn(self,thread,turn):
        # Read persisted completion independently of the live notification pipe.
        reader=Bridge()
        try:return reader.read_turn(thread,turn)
        finally:reader.close()

    def rpc(self, *args, **kwargs):
        return codex.rpc(*args, **kwargs)

    def test_connection(self):
        thread, turn, events, model = self.begin('Reply only: ECONLAB_OK')
        answer, deadline, finished = '', time.monotonic() + 60, False
        try:
            while time.monotonic() < deadline:
                try: msg = events.get(timeout=1)
                except queue.Empty: continue
                params = msg.get('params', {})
                if msg['method'] == 'item/agentMessage/delta':
                    answer += params.get('delta', '')
                elif msg['method'] == 'item/completed' and params.get('item', {}).get('type') == 'agentMessage':
                    answer = params['item'].get('text') or answer
                elif msg['method'] == 'bridge/error':
                    raise ValueError(params.get('message', 'Codex connection lost'))
                elif msg['method'] == 'turn/completed':
                    finished=True
                    state = params.get('turn', {})
                    if state.get('status') != 'completed':
                        raise ValueError((state.get('error') or {}).get('message', 'Codex connection test failed'))
                    if 'ECONLAB_OK' not in answer:
                        raise ValueError('Codex did not return the expected test reply')
                    return {'ok': True, 'provider': 'codex', 'model': model, 'reply': answer.strip(), 'paper_content_sent': False}
            raise ValueError('Codex connection test timed out')
        finally:
            if not finished:
                try:self.interrupt(thread, turn)
                except Exception:pass
            self.listeners.pop(thread, None)

    def close(self):
        codex.close()
        agent.close()

bridge = ProviderBridge()
