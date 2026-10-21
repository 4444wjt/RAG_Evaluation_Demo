import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location("pilot",Path(__file__).with_name("run_pilot.py"))
pilot=importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class PilotSafety(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.budget=pilot.Budget(self.root/"ledger.jsonl")
        self.model=pilot.model_specs()[0]

    def tearDown(self):self.tmp.cleanup()

    def test_cannot_send_without_full_reservation(self):
        tiny=pilot.Budget(self.root/"tiny.jsonl",pilot.Decimal("0.01"))
        with self.assertRaises(pilot.StopPilot):tiny.reserve("r",self.model,{})
        self.assertFalse((self.root/"tiny.jsonl").exists())

    def test_no_repeat_reserved_or_settled_request(self):
        self.budget.reserve("r",self.model,{})
        self.budget.settle("r",self.model,dict(prompt_tokens=100,completion_tokens=20,total_tokens=120))
        with self.assertRaises(pilot.StopPilot):self.budget.reserve("r",self.model,{})

    def test_unknown_usage_holds_full_reservation_after_restart(self):
        self.budget.reserve("r",self.model,{})
        with self.assertRaises(pilot.StopPilot):self.budget.settle("r",self.model,None)
        recovered=pilot.Budget(self.budget.path)
        self.assertEqual(recovered.balances(),(pilot.Decimal(0),pilot.upper_cost(self.model)))

    def test_invalid_usage_types_and_caps_not_free(self):
        for usage in [dict(prompt_tokens="100",completion_tokens=1,total_tokens=101),
                      dict(prompt_tokens=True,completion_tokens=1,total_tokens=2),
                      dict(prompt_tokens=100,completion_tokens=3000,total_tokens=3100),
                      dict(prompt_tokens=100,completion_tokens=1,total_tokens=99)]:
            with self.subTest(usage=usage):
                self.budget.reserve(str(usage),self.model,{})
                with self.assertRaises(pilot.StopPilot):self.budget.settle(str(usage),self.model,usage)
        self.assertGreater(self.budget.balances()[1],0)

    def test_known_usage_releases_only_unused_reservation(self):
        self.budget.reserve("r",self.model,{})
        self.budget.settle("r",self.model,dict(prompt_tokens=1000,completion_tokens=100,total_tokens=1100))
        known,held=self.budget.balances()
        self.assertEqual(known,pilot.Decimal("0.004543"))
        self.assertEqual(held,0)

    def test_secret_write_refused(self):
        pilot.SECRETS[:]=[b"synthetic-test-secret"]
        try:
            with self.assertRaises(RuntimeError):pilot.write_json(self.root/"bad.json",{"test":"synthetic-test-secret"})
            self.assertFalse((self.root/"bad.json").exists())
        finally:pilot.SECRETS[:]=[]

    def test_no_redirect(self):
        handler=pilot.NoRedirect()
        self.assertIsNone(handler.redirect_request(None,None,302,"",{},"https://invalid.example"))

    def test_second_runner_lock_is_rejected(self):
        lock=self.root/"pilot.lock"
        with pilot.execution_lock(lock):
            with self.assertRaises(pilot.StopPilot):
                with pilot.execution_lock(lock):self.fail("second runner entered")
        self.assertFalse(lock.exists())

    def test_real_adapter_reserves_sends_once_and_settles(self):
        class Prompt:
            def to_string(self):return "fixed synthetic prompt"
        class Response:
            status=200
            headers={"x-request-id":"synthetic-request-id"}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return pilot.encode(dict(id="synthetic-response",model="synthetic-model",usage=dict(prompt_tokens=100,completion_tokens=20,total_tokens=120),choices=[dict(finish_reason="stop",message=dict(content="[]"))]))
        class Opener:
            def __init__(self):self.sent=0
            def open(self,req,timeout):
                self.sent+=1
                assert self.budget.balances()[1]>0
                return Response()
        opener=Opener();opener.budget=self.budget
        combo=dict(combination_id="test-qwen72b",case_id="synthetic",judge_id="qwen72b",repeat=1,input_sha256="synthetic-sha")
        (self.root/"requests").mkdir()
        with patch.object(pilot,"OUT",self.root),patch.object(pilot.time,"sleep"):
            adapter=pilot.PilotLLM(self.model,combo,self.budget,"synthetic-not-a-real-key",opener=opener)
            result=asyncio.run(adapter.generate(Prompt()))
        self.assertEqual(opener.sent,1)
        self.assertEqual(result.generations[0][0].text,"[]")
        self.assertEqual(self.budget.balances()[1],0)
        self.assertTrue((self.root/"requests/test-qwen72b-extraction.request.json").exists())

    def test_original_metric_duck_typing_has_two_calls_no_repair(self):
        from langchain_core.outputs import Generation,LLMResult
        class Fake:
            def __init__(self,bad=False):self.calls=[];self.bad=bad
            async def generate(self,prompt,**kwargs):
                self.calls.append(prompt.to_string())
                text='not-json' if self.bad else ('[{"sentence_index":0,"simpler_statements":["存款上限50万元。"]}]' if len(self.calls)==1 else '[{"statement":"存款上限50万元。","reason":"原文明确。","verdict":1}]')
                return LLMResult(generations=[[Generation(text=text)]])
        Metric=pilot.metric_class()
        row=dict(question="上限？",answer="存款上限50万元。",contexts=["存款上限50万元。"])
        fake=Fake()
        score=asyncio.run(Metric(llm=fake,max_retries=0)._ascore(row,callbacks=[]))
        self.assertEqual(score,1)
        self.assertEqual(len(fake.calls),2)
        fake=Fake(bad=True)
        score=asyncio.run(Metric(llm=fake,max_retries=0)._ascore(row,callbacks=[]))
        self.assertTrue(pilot.math.isnan(score))
        self.assertEqual(len(fake.calls),1)


if __name__=="__main__":unittest.main(verbosity=2)
