import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import ai_editor as ai


class Reply:
    status_code=200
    def __init__(self,result=None,status='completed',refusal=False):
        self.result=result; self.status=status; self.refusal=refusal
    def json(self):
        part={'type':'refusal','refusal':'no'} if self.refusal else {'type':'output_text','text':json.dumps(self.result)}
        return {'status':self.status,'output':[{'type':'message','content':[part]}]}


class Session:
    def __init__(self,reply): self.reply=reply; self.calls=[]
    def post(self,*args,**kwargs): self.calls.append((args,kwargs)); return self.reply


class FixtureAI:
    """Deterministic API fixture, not a claim of live vision accuracy."""
    def __init__(self): self.inputs=[]
    def ask(self,instruction,content,schema,name):
        self.inputs.append((name,content))
        first=json.loads(content[0]['text'])
        if name=='visual_scenes':
            times=[float(c['text'].split()[2][:-1]) for c in content if c['type']=='input_text' and c['text'].startswith('원본 시각')]
            return {'scenes':[{'window_id':first['window_id'],'start':first['start'],'end':times[-1],
                'usable':True,'description':'도구를 사용한 작업 장면','visible_action':'표면을 닦는 작업',
                'stage':'작업','reason':'작업 과정이 보임','confidence':0.9,'evidence_times':[times[0],times[-1]]}]}
        if name=='edit_plan':
            return {'cuts':[{'scene_id':c['scene_id'],'start':c['start'],'end':c['end'],'reason':'실제 작업'} for c in first['catalog']], 'summary':'작업 순서'}
        return {'lines':[{'cut_id':f'cut_{i+1}','text':'표면을 확인하며 작업합니다.','visual_evidence':'선택 컷의 작업 모습'} for i in range(first['cut_count'])]}


class VisionTests(unittest.TestCase):
    def scene(self): return ai.Scene('s0','clip',0,4,'visible','action','stage','reason',0.9)

    def test_http_contract_uses_images_and_no_server_storage(self):
        session=Session(Reply({'ok':True})); client=ai.VisionClient('test-only',session=session)
        image={'type':'input_image','image_url':'data:image/jpeg;base64,AA==','detail':'high'}
        self.assertEqual(client.ask('inspect',[image],ai.obj({'ok':{'type':'boolean'}}),'test'),{'ok':True})
        payload=session.calls[0][1]['json']
        self.assertFalse(payload['store']); self.assertTrue(payload['text']['format']['strict'])
        self.assertEqual(payload['input'][0]['content'][0],image)

    def test_key_missing_is_not_template_fallback(self):
        with self.assertRaises(ai.AIError): ai.VisionClient('')

    def test_refusal_and_incomplete_are_failures(self):
        for reply in [Reply({},status='incomplete'),Reply(refusal=True)]:
            with self.assertRaises(ai.AIError): ai.VisionClient('test-only',session=Session(reply)).ask('',[],{},'test')

    def test_http_error_never_echoes_response_or_key(self):
        reply=Reply(); reply.status_code=401
        with self.assertRaises(ai.AIError) as raised:
            ai.VisionClient('test-only',session=Session(reply)).ask('',[],{},'test')
        self.assertNotIn('test-only',str(raised.exception))

    def test_unknown_scene_rejected(self):
        client=unittest.mock.Mock(); client.ask.return_value={'cuts':[{'scene_id':'unknown','start':0,'end':2,'reason':'x'}]}
        with self.assertRaises(ai.AIError): ai.plan_timeline([self.scene()],40,client,'')

    def test_duplicate_and_outside_cuts_rejected(self):
        valid={'scene_id':'s0','start':0,'end':3,'reason':'x'}
        for cuts in [[valid,valid],[dict(valid,end=5)]]:
            client=unittest.mock.Mock(); client.ask.return_value={'cuts':cuts}
            with self.assertRaises(ai.AIError): ai.plan_timeline([self.scene()],40,client,'')

    def test_target_overrun_rejected(self):
        client=unittest.mock.Mock(); client.ask.return_value={'cuts':[{'scene_id':'s0','start':0,'end':4,'reason':'x'}]}
        with self.assertRaises(ai.AIError): ai.plan_timeline([self.scene()],3,client,'')

    def test_short_valid_timeline_does_not_duplicate(self):
        client=unittest.mock.Mock(); client.ask.return_value={'cuts':[{'scene_id':'s0','start':0,'end':4,'reason':'x'}]}
        self.assertEqual(len(ai.plan_timeline([self.scene()],60,client,'')),1)

    def test_frame_limit_before_any_upload(self):
        client=unittest.mock.Mock()
        with patch.object(ai,'media_info',return_value=[('clip',5000)]):
            with self.assertRaises(ai.AIError): ai.analyze_sources(['clip'],client,'')
        client.ask.assert_not_called()

    def test_real_frame_extraction_and_final_cut_reinspection(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'clip.avi'
            writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),10,(64,64))
            for i in range(40): writer.write(np.full((64,64,3),(i*5,40,150),dtype=np.uint8))
            writer.release()
            client=FixtureAI()
            scenes=ai.analyze_sources([str(path)],client,'작업',1)
            cuts=ai.plan_timeline(scenes,40,client,'작업')
            lines=ai.write_script(cuts,client,'작업','자극적')
            self.assertEqual(len(lines),len(cuts))
            vision_images=[c for c in client.inputs[0][1] if c['type']=='input_image']
            final_images=[c for c in client.inputs[-1][1] if c['type']=='input_image']
            self.assertGreaterEqual(len(vision_images),4); self.assertEqual(len(final_images),3)
            self.assertTrue(all(c['image_url'].startswith('data:image/jpeg;base64,') for c in final_images))
            import app
            with patch.object(ai,'VisionClient',return_value=FixtureAI()), patch.object(app,'render_video') as render:
                worker=app.AIEditWorker(([str(path)],40,'test-only',ai.DEFAULT_MODEL,'작업','자극적',1.0),{'out_mp4':str(Path(d)/'output.mp4')})
                errors=[]; worker.failed.connect(errors.append); worker.run()
                self.assertEqual(errors,[])
                render.assert_called_once()
                generated=render.call_args.args[0]
                self.assertEqual(generated[0].line,'표면을 확인하며 작업합니다.')
                self.assertTrue(generated[0].visual_description)
                self.assertTrue(generated[0].visual_evidence)

    def test_narration_must_match_cut_order(self):
        client=unittest.mock.Mock(); client.ask.return_value={'lines':[{'cut_id':'cut_2','text':'작업','visual_evidence':'x'}]}
        with patch.object(ai,'frame_input',return_value=[]):
            with self.assertRaises(ai.AIError): ai.write_script([self.scene()],client,'','')


if __name__=='__main__': unittest.main()
