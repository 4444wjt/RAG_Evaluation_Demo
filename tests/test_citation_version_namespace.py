"""Bounded namespace expansion checks; never allocate the large expanded tree."""
import base64
import json

import pytest

from backend.core import citation_version as cv
from test_citation_version import XML, body, inspection, make_request


def namespace_source(kind="elements", *, namespace_size=8192, names=2500):
    namespace = "urn:" + "n" * (namespace_size - 4)
    if kind == "attributes":
        fragment = '<Holder xmlns:x="' + namespace + '"' + "".join(f' x:a{i}="v"' for i in range(names)) + "/>"
    elif kind == "default":
        fragment = '<Holder xmlns="' + namespace + '">' + "".join(f"<n{i}/>" for i in range(names)) + "</Holder>"
    elif kind == "rebind":
        fragment = '<Holder xmlns:x="urn:short"><Holder xmlns:x="' + namespace + '">' + "".join(f"<x:n{i}/>" for i in range(names)) + "</Holder></Holder>"
    else:
        fragment = '<Holder xmlns:x="' + namespace + '">' + "".join(f"<x:n{i}/>" for i in range(names)) + "</Holder>"
    return XML.replace(b"</Legislation>", fragment.encode("utf-8") + b"</Legislation>")


@pytest.mark.parametrize("kind", ["elements", "attributes", "default", "rebind"])
@pytest.mark.parametrize("operation", ["inspect", "check", "evaluate"])
def test_name_expansion_is_rejected_before_elementtree_allocation(monkeypatch, kind, operation):
    raw = namespace_source(kind)
    assert len(raw) < 50000
    def forbidden(*_args, **_kwargs):
        raise AssertionError("namespace rejection must occur before ElementTree or B1")
    monkeypatch.setattr(cv.ET, "iterparse", forbidden)
    monkeypatch.setattr(cv.rules, "evaluate", forbidden)
    if operation == "inspect":
        row = cv.inspect(inspection(raw))["files"][0]
        assert row["status"] == "INVALID_INPUT" and "命名空间" in row["error"]
    else:
        output = cv.check(body(raw))["rows"][0]["output"] if operation == "check" else cv.evaluate(raw, make_request(raw))
        assert output["axes"] == dict.fromkeys(cv.rules.AXES, "INVALID_INPUT")
        assert "命名空间" in output["input_errors"][0]


@pytest.mark.parametrize("fragment", [
    '<Holder xmlns:x="urn:first"><x:a/><Holder xmlns:x="urn:second"><x:b x:k="1"/></Holder><x:c/></Holder>',
    '<Holder xmlns="urn:first"><a/><Holder xmlns=""><b/></Holder><c/></Holder>',
    '<Holder xmlns:x="urn:名字"><x:项 x:属性="值"/></Holder>',
    '<x:Holder xmlns:x="urn:declaration-on-self" x:attribute="value"/>',
])
def test_small_namespace_variants_keep_unmodified_b1_output(fragment):
    raw = XML.replace(b"</Legislation>", fragment.encode("utf-8") + b"</Legislation>")
    assert cv.inspect(inspection(raw))["files"][0]["status"] == "READY"
    assert cv.evaluate(raw, make_request(raw)) == cv.rules.evaluate(raw, make_request(raw), "B1")


def test_invalid_namespace_still_invalid_without_changing_other_rows():
    request = body()
    request["files"][0]["base64"] = base64.b64encode(namespace_source()).decode("ascii")
    request["citations"][0]["request"] = make_request(namespace_source())
    request["files"].extend(body()["files"])
    request["files"][1]["id"] = "valid"
    request["citations"].append(dict(body()["citations"][0], id="valid", file_id="valid"))
    report = cv.check(request)
    assert report["counts"]["total"] == 2 and report["counts"]["invalid"] == 1
    assert report["rows"][1]["output"] == cv.rules.evaluate(XML, make_request(), "B1")
    assert json.loads(json.dumps(report))["rows"][0]["id"] == "c1"
