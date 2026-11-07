"""Compatible service exports, loaded only when that service is requested."""

from importlib import import_module

__all__ = [
    "load_and_split_doc",
    "build_vector_store",
    "rag_answer",
    "generate_testset",
    "load_custom_testset",
    "load_benchmark",
    "generate_negative_testset",
    "_negative_question_key",
    "_negative_context",
    "_fallback_negative_testset",
    "trace_hallucinations",
    "trace_unavailable",
    "TRACE_PROMPT_TEMPLATE",
]

_EXPORTS = {
    "load_and_split_doc": "documents",
    "build_vector_store": "retriever",
    "rag_answer": "answering",
    "generate_testset": "testsets",
    "load_custom_testset": "testsets",
    "load_benchmark": "testsets",
    "generate_negative_testset": "negatives",
    "_negative_question_key": "negatives",
    "_negative_context": "negatives",
    "_fallback_negative_testset": "negatives",
    "trace_hallucinations": "trace",
    "trace_unavailable": "trace",
    "TRACE_PROMPT_TEMPLATE": "trace",
}


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
