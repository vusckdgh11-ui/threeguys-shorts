import json
import unittest
from unittest.mock import patch
import ai_editor as ai

class Session:
    status_code=200
    def __init__(self): self.calls=[]
    def post(self,*args,**kwargs): self.calls.append((args,kwargs)); return self
    def json(self): return {'done':True,'message':{'content':'{"result":"ok"}'}}

class LocalTests(unittest.TestCase):
    @patch('local_runtime.ensure_server')
    def test_local_images_schema_and_no_paid_call(self,server):
        session=Session();client=ai.LocalVisionClient(session=session)
        result=client.ask('inspect',[ai.text('time 1'),{'type':'input_image','image_url':'data:image/jpeg;base64,YQ=='},ai.text('time 2'),{'type':'input_image','image_url':'data:image/jpeg;base64,Yg=='}],ai.obj({'result':ai.STRING}),'test')
        args,kw=session.calls[0]
        self.assertEqual(args,('http://127.0.0.1:11435/api/chat',))
        self.assertNotIn('headers',kw)
        self.assertEqual(kw['json']['messages'][1]['images'],['YQ=='])
        self.assertEqual(kw['json']['messages'][2]['content'],'time 2')
        self.assertEqual(result,{'result':'ok'})
        self.assertFalse(session.trust_env)
    @patch('local_runtime.ensure_server')
    def test_local_error_never_falls_back(self,server):
        s=Session();s.status_code=500
        with self.assertRaises(ai.AIError): ai.LocalVisionClient(session=s).ask('',[],{},'test')
        self.assertEqual(len(s.calls),1)
    def test_factory_and_cloud_guard(self):
        self.assertIsInstance(ai.create_client('',ai.LOCAL_MODEL),ai.LocalVisionClient)
        with self.assertRaises(ai.AIError): ai.create_client('','ollama:model-cloud')
        with self.assertRaises(ai.AIError): ai.create_client('','gpt-4.1-mini')

if __name__=='__main__': unittest.main()
