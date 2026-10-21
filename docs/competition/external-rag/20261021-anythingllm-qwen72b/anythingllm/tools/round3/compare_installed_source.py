"""Compare an installed AnythingLLM Desktop server tree with audited v1.18.0 files.

Standard library only; reads files, never executes them, never calls a network.
Output paths are relative to --install-dir so no machine path is recorded.

  build-reference   write reference-v1.18.0.json from the locally saved tag copies
  compare           hash the installed copies and scan for moment locale changes
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys

RELEASE_TAG = "v1.18.0"
RELEASE_COMMIT = "3e4f9411b67f2dd7fa9fc6f2b95dfacbda0633ba"
HERE = Path(__file__).resolve().parent
WORK = HERE.parent
# Paths are relative to the repository's server/ directory.
REFERENCE_SOURCES = {
    "endpoints/api/workspace/index.js": f"round2/research-release/{RELEASE_COMMIT}/server/endpoints/api/workspace/index.js",
    "utils/AiProviders/deepseek/index.js": f"round2/research-release/{RELEASE_COMMIT}/server/utils/AiProviders/deepseek/index.js",
    "utils/AiProviders/modelMap/index.js": f"round2/research-release/{RELEASE_COMMIT}/server/utils/AiProviders/modelMap/index.js",
    "utils/chats/apiChatHandler.js": f"round2/research-release/{RELEASE_COMMIT}/server/utils/chats/apiChatHandler.js",
    "utils/helpers/chat/index.js": f"round2/research-release/{RELEASE_COMMIT}/server/utils/helpers/chat/index.js",
    "utils/helpers/tiktoken.js": f"round2/research-release/{RELEASE_COMMIT}/server/utils/helpers/tiktoken.js",
    "models/systemPromptVariables.js": "round2/research-bound/v1.18.0/server/models/systemPromptVariables.js",
    "models/systemSettings.js": "round2/research-bound/v1.18.0/server/models/systemSettings.js",
    "utils/chats/index.js": "round2/research-bound/v1.18.0/server/utils/chats/index.js",
    "utils/memories/index.js": "round2/research-bound/v1.18.0/server/utils/memories/index.js",
    "utils/TextSplitter/index.js": "round2/research-bound/v1.18.0/server/utils/TextSplitter/index.js",
    "utils/vectorDbProviders/lance/index.js": "round2/research-bound/v1.18.0/server/utils/vectorDbProviders/lance/index.js",
    "endpoints/api/system/index.js": "round3/upstream/api_system_index.js",
    "utils/helpers/index.js": "round3/upstream/helpers_index.js",
}
# The {datetime} prompt variable reads moment's global locale (systemPromptVariables.js:41).
# Loading a moment locale bundle switches it wherever that happens.
LOCALE_PATTERN = re.compile(rb"moment/locale/|moment/min/locales|moment-with-locales")
# Calls that can switch it, under any receiver name (aliases, minified names). They only
# reach moment from a file that imports it; elsewhere (dayjs.locale, y18n.updateLocale)
# they are reported as information, not counted. A counted false hit only costs the
# 1,024-byte assumption, never a proof.
LOCALE_CALL = re.compile(rb"\.\s*locale\s*\(\s*[^)\s]|\bdefineLocale\s*\(|\bupdateLocale\s*\(")
IMPORTS_MOMENT = re.compile(rb"""(?:require\s*\(\s*|\bfrom\s+|\bimport\s*\(\s*|\bimport\s+)["'`]moment["'`/]""")
SCRIPT_SUFFIXES = (".js", ".cjs", ".mjs")
# Built frontend that the server only serves to browsers as static files.
STATIC_DIRS = ("public",)


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def build_reference(output: Path) -> dict:
    files = {}
    for rel, local in REFERENCE_SOURCES.items():
        data = (WORK / local).read_bytes()
        files[rel] = {"sha256": sha256(data).hexdigest(), "bytes": len(data), "localCopy": local}
    manifest = {"schemaVersion": "anythingllm-reference.v1", "tag": RELEASE_TAG, "commit": RELEASE_COMMIT,
                "files": files,
                "note": "各文件为从 GitHub raw 以该 commit 取得的字节副本，取得记录见 round2/research-release 与 round3/upstream。"}
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def compare(install_dir: Path, reference: dict) -> dict:
    root = install_dir.resolve()
    wanted = {rel: [] for rel in reference["files"]}
    asar = []
    scripts = []
    for current, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            path = Path(current, name)
            posix = _rel(path, root)
            if name.endswith(".asar"):
                asar.append(posix)
            if name.endswith(SCRIPT_SUFFIXES):
                scripts.append(path)
            if "/node_modules/" in "/" + posix:
                continue  # audited files are AnythingLLM's own server code, never a dependency
            for rel in wanted:
                if posix == rel or posix.endswith("/" + rel):
                    wanted[rel].append(path)

    files = {}
    server_roots = set()
    for rel, matches in wanted.items():
        expected = reference["files"][rel]["sha256"]
        entries = []
        for path in matches:
            digest = file_sha256(path)
            entries.append({"path": _rel(path, root), "sha256": digest, "matchesReference": digest == expected})
        if not entries:
            status = "not_found"
        elif len(entries) > 1:
            status = "multiple_copies"
        else:
            status = "match" if entries[0]["matchesReference"] else "mismatch"
            server_roots.add(entries[0]["path"][: -len(rel)].rstrip("/"))
        files[rel] = {"status": status, "expectedSha256": expected, "found": entries}

    all_matched = bool(files) and all(item["status"] == "match" for item in files.values())
    single_root = len(server_roots) == 1
    locale = {"scanned": False, "scope": None, "scriptFiles": 0, "staticFilesSkipped": 0, "hits": [],
              "informationalHits": []}
    if all_matched and single_root:
        server_root = (root / next(iter(server_roots))) if next(iter(server_roots)) else root
        locale["scope"] = _rel(server_root, root) or "."
        for path in scripts:
            try:
                path.relative_to(server_root)
            except ValueError:
                continue
            posix = _rel(path, root)
            # moment's own locale files define locales but are only loaded when required.
            if "/node_modules/moment/" in "/" + posix:
                continue
            if path.relative_to(server_root).parts[0] in STATIC_DIRS:
                locale["staticFilesSkipped"] += 1
                continue
            locale["scriptFiles"] += 1
            data = path.read_bytes()
            imports_moment = IMPORTS_MOMENT.search(data) is not None
            for number, line in enumerate(data.splitlines(), 1):
                if LOCALE_PATTERN.search(line) or (imports_moment and LOCALE_CALL.search(line)):
                    locale["hits"].append({"path": posix, "line": number})
                elif LOCALE_CALL.search(line):
                    locale["informationalHits"].append({"path": posix, "line": number,
                                                        "reason": "file_does_not_import_moment"})
        locale["scanned"] = True
    return {
        "schemaVersion": "anythingllm-installed-source.v1",
        "referenceTag": reference["tag"], "referenceCommit": reference["commit"],
        "allAuditedFilesMatched": all_matched, "singleServerRoot": single_root,
        "serverRoot": next(iter(server_roots)) if single_root else None,
        "files": files, "asarArchives": asar, "localeScan": locale,
        "note": "只比对字节与扫描文本；找不到、多份或不一致都不当作已确认。路径相对安装目录。",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-reference")
    build.add_argument("--output", type=Path, default=HERE / "reference-v1.18.0.json")
    run = sub.add_parser("compare")
    run.add_argument("--install-dir", type=Path, required=True)
    run.add_argument("--reference", type=Path, default=HERE / "reference-v1.18.0.json")
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build-reference":
            manifest = build_reference(args.output)
            print(f"参考清单：{len(manifest['files'])} 个文件")
            return 0
        if not args.install_dir.is_dir():
            raise ValueError("安装目录不存在")
        reference = json.loads(args.reference.read_text(encoding="utf-8"))
        result = compare(args.install_dir, reference)
        with open(args.output, "x", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    except (OSError, ValueError) as error:
        print(f"未完成：{type(error).__name__}：{error}", file=sys.stderr)
        return 2
    matched = sum(item["status"] == "match" for item in result["files"].values())
    print(f"一致 {matched}/{len(result['files'])}；asar {len(result['asarArchives'])} 个；"
          f"locale 扫描：{'未执行' if not result['localeScan']['scanned'] else str(len(result['localeScan']['hits'])) + ' 处'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
