"""Shared judge construction and observed model provenance; never infer response names."""

from contextlib import contextmanager
from hashlib import sha256
import json
from threading import Lock

from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI


class ObservedJudge(ChatOpenAI):
    """Keep the raw response identity before the SDK substitutes the request name."""

    def _create_chat_result(self, response, generation_info=None):
        raw = response if isinstance(response, dict) else response.model_dump()
        returned = raw.get("model")
        returned = returned if isinstance(returned, str) and returned else None
        result = super()._create_chat_result(response, generation_info)
        result.llm_output["model_name"] = returned
        result.llm_output["veriscope_response_model"] = returned
        for generation in result.generations:
            generation.message.response_metadata["veriscope_response_model"] = returned
            generation.message.response_metadata["veriscope_response_model_recorded"] = True
        return result

    def _combine_llm_outputs(self, llm_outputs):
        combined = super()._combine_llm_outputs(llm_outputs)
        names = list(dict.fromkeys(output["veriscope_response_model"] for output in llm_outputs
                                   if isinstance(output, dict) and isinstance(output.get("veriscope_response_model"), str)))
        combined["model_name"] = names[0] if len(names) == 1 else None
        return combined


def observe_judge(llm):
    """Clone already-validated fields, retaining the original clients and generation parameters.

    No second client/model configuration is built and no provider request is made here.
    The subclass changes response metadata only; arbitrary injected test models stay intact.
    """
    if isinstance(llm, ChatOpenAI) and not isinstance(llm, ObservedJudge):
        return ObservedJudge.construct(_fields_set=llm.__fields_set__, **llm.__dict__)
    return llm


def build_judge(model_config, model_factory=None):
    """Use the same model adapter as the judge-comparison experiment."""
    if model_factory is None:
        from .registry import build_llm
        model_factory = build_llm
    return observe_judge(model_factory(model_config))


class ModelCallRecorder(BaseCallbackHandler):
    """Capture returned identities and usage, without prompts, credentials or error text."""

    def __init__(self):
        self.events = []
        self._requests = {}
        self._lock = Lock()

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        parameters = kwargs.get("invocation_params") or {}
        content = json.dumps([[message.content for message in batch] for batch in messages], ensure_ascii=False, default=str)
        request = {"prompt_sha256": sha256(content.encode("utf-8")).hexdigest(),
                   "request_parameters": {key: parameters[key] for key in ("model", "model_name", "temperature", "n", "max_tokens")
                                          if key in parameters and isinstance(parameters[key], (str, int, float, bool, type(None)))}}
        with self._lock:
            self._requests[str(run_id)] = request

    def on_llm_end(self, response, *, run_id, **kwargs):
        output = response.llm_output if isinstance(response.llm_output, dict) else {}
        names = []
        # The SDK's llm_output.model_name may be the requested name, even when the
        # provider returned a different identity or no identity. Never use that fallback.
        value = output.get("veriscope_response_model")
        if isinstance(value, str) and value:
            names.append(value)
        for batch in response.generations:
            for generation in batch:
                metadata = getattr(getattr(generation, "message", None), "response_metadata", {}) or {}
                if metadata.get("veriscope_response_model_recorded") is True:
                    value = metadata.get("veriscope_response_model")
                    if isinstance(value, str) and value and value not in names:
                        names.append(value)
        usage = output.get("token_usage") or output.get("usage") or {}
        usage = usage if isinstance(usage, dict) else {}
        safe_usage = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe_usage[key] = value
        with self._lock:
            self.events.append({**self._requests.pop(str(run_id), {}), "status": "completed", "run_id": str(run_id),
                                "response_models": names, "usage": safe_usage})

    def on_llm_error(self, error, *, run_id, **kwargs):
        with self._lock:
            self.events.append({**self._requests.pop(str(run_id), {}), "status": "error", "run_id": str(run_id),
                                "error_type": type(error).__name__})

    @property
    def response_models(self):
        return list(dict.fromkeys(name for event in self.events for name in event.get("response_models", [])))

    def snapshot(self):
        with self._lock:
            return [*self.events, *({**request, "run_id": run_id, "status": "incomplete", "usage": {}}
                                    for run_id, request in self._requests.items())]

    @contextmanager
    def capture(self, llm):
        """Attach locally; preserve existing observers and restore callbacks even after failure."""
        previous = getattr(llm, "callbacks", None)
        attached = False
        try:
            if previous is None or isinstance(previous, list):
                callbacks = [*(previous or []), self]
            else:
                callbacks = previous.copy()
                callbacks.add_handler(self)
            llm.callbacks = callbacks
            attached = True
        except (AttributeError, TypeError):
            # Injected offline stubs may not implement callbacks; names stay explicitly unknown.
            pass
        try:
            yield self
        finally:
            if attached:
                llm.callbacks = previous


def judge_provenance(config, recorder, *, configured):
    return {"judge_model": config["name"], "judge_mode": "configured" if configured else "self",
            "judge_response_scope": "model_batch",
            "judge_response_models": recorder.response_models,
            "judge_calls": recorder.snapshot()}
