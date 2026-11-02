import argparse
import collections
import functools
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from select_tests import FULL, LOCAL, changed_paths as read_changed_paths, load_graph

HOST_BROAD_CHANGES = {"CMakeLists.txt", ".github/workflows/build.yml", "tools/build_mingw_host_only_tests.py"}
HEADERS = (".h", ".hh", ".hpp", ".inl")
INCLUDED = HEADERS + (".hxx", ".h++", ".inc", ".ipp", ".tcc", ".tpp", ".def", ".manifest")
COMPILED = (".c", ".cc", ".cpp", ".cxx", ".c++", ".m", ".mm", ".s", ".asm", ".rc",
            ".comp", ".frag", ".vert", ".geom", ".tesc", ".tese", ".glsl")
BUILD_FILES = re.compile(f"(?:{FULL.pattern})|(?:{LOCAL.pattern})", re.IGNORECASE)


def object_document(value, path, name):
    if not isinstance(value, dict):
        raise ValueError(f"{path}: invalid {name} (expected an object)")
    return value


def object_entries(value, path, name):
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{path}: invalid {name} (expected a list of objects)")
    return value


def target_document(value, path):
    target = object_document(value, path, "CMake target")
    if "name" in target and not isinstance(target["name"], str):
        raise ValueError(f"{path}: invalid CMake target name (expected a string)")
    link = object_document(target.get("link", {}), path, "CMake target link")
    object_entries(link.get("commandFragments", []), path, "CMake link command fragments")
    object_entries(target.get("artifacts", []), path, "CMake target artifacts")
    object_entries(target.get("sources", []), path, "CMake target sources")
    return target


def read_reply(build_dir):
    reply_dir = build_dir / ".cmake" / "api" / "v1" / "reply"
    indexes = list(reply_dir.glob("index-*.json"))
    if not indexes:
        raise ValueError(f"{reply_dir}: no CMake File API index; configure with the codemodel-v2 query first")
    index = max(indexes, key=lambda path: path.stat().st_mtime_ns)
    document = object_document(json.loads(index.read_text(encoding="utf-8")), index, "CMake File API index")
    reply = object_document(document.get("reply", {}), index, "CMake File API reply")
    codemodel = reply.get("codemodel-v2")
    if codemodel is None:
        raise ValueError(f"{index}: missing codemodel-v2 reply")
    codemodel = object_document(codemodel, index, "CMake codemodel reference")
    if not codemodel:
        raise ValueError(f"{index}: missing codemodel-v2 reply")
    model_path = reply_dir / codemodel["jsonFile"]
    model = object_document(json.loads(model_path.read_text(encoding="utf-8")), model_path, "CMake codemodel")
    configurations = model.get("configurations", [])
    if len(configurations) != 1:
        raise ValueError(f"{index}: expected one CMake configuration, found {len(configurations)}")
    configuration = object_document(configurations[0], model_path, "CMake configuration")
    targets = {}
    for target in configuration.get("targets", []):
        target = object_document(target, model_path, "CMake target reference")
        target_path = reply_dir / target["jsonFile"]
        targets[target["id"]] = target_document(
            json.loads(target_path.read_text(encoding="utf-8")), target_path)
    return Path(model["paths"]["source"]).resolve(), targets


def link_command(target):
    link = target.get("link", {})
    fragments = link.get("commandFragments", [])
    return " ".join(fragment.get("fragment", "") for fragment in fragments)


def is_libc_link(name, target):
    if name == "libc":
        return True
    command = link_command(target)
    return re.search(r"(?i)(?:^|[/\\\s])(?:libc\.prx|(?:lib)?libc\.dll\.a)(?:$|[\s\"'])", command) is not None


def links_guest_libc(target):
    return is_libc_link(target["name"], target)


def is_core_libs_test_source(source_path, core_libs):
    try:
        relative = source_path.resolve().relative_to(core_libs)
    except ValueError:
        return False
    return "tests" in relative.parent.parts


def is_product_library(target, source_root):
    if target.get("type") not in {"STATIC_LIBRARY", "SHARED_LIBRARY", "MODULE_LIBRARY"}:
        return False
    source_dir = (source_root / target["paths"]["source"]).resolve()
    core_libs = (source_root / "core" / "libs").resolve()
    try:
        source_dir.relative_to(core_libs)
    except ValueError:
        return False
    sources = target.get("sources", [])
    if not isinstance(sources, list) or any(not isinstance(source, dict) for source in sources):
        raise ValueError("invalid library sources: expected a list of objects")
    original_sources = [source_root / source["path"] for source in sources if not source.get("isGenerated")]
    if not original_sources:
        try:
            source_dir.relative_to(core_libs / "tests")
        except ValueError:
            return True
        return False
    return not all(is_core_libs_test_source(source, core_libs) for source in original_sources)


def link_command_mentions_target(command, target):
    link_names = set()
    for artifact in target.get("artifacts", []):
        name = Path(artifact.get("path", "")).name
        if name and re.search(rf"(?i)(?:^|[/\\:=\s\"',]){re.escape(name)}(?=$|[\s\"',])", command):
            return True
        for suffix in (".dll.a", ".dylib", ".prx", ".so", ".a", ".lib"):
            if name.casefold().endswith(suffix):
                name = name[:-len(suffix)]
                break
        if name.startswith("lib") and target.get("name", "").casefold() != "libc":
            name = name[3:]
        if name:
            link_names.add(name)
    target_name = target.get("name", "")
    if target_name:
        link_names.add(target_name)
    return any(
        re.search(rf"(?i)(?:^|\s)-l{re.escape(name)}(?=$|\s)", command) is not None
        for name in link_names
    )


def links_product_library(target, targets, source_root):
    if links_guest_libc(target):
        return True
    pending = [target]
    seen = set()
    while pending:
        current = pending.pop()
        command = link_command(current)
        if not command:
            continue
        for candidate_id, candidate in targets.items():
            if candidate_id in seen or candidate.get("type") not in {"STATIC_LIBRARY", "SHARED_LIBRARY", "MODULE_LIBRARY"}:
                continue
            if not link_command_mentions_target(command, candidate):
                continue
            if is_product_library(candidate, source_root) or links_guest_libc(candidate):
                return True
            seen.add(candidate_id)
            pending.append(candidate)
    return False


def source_relative_path(source_root, source):
    try:
        return source.relative_to(source_root).as_posix()
    except ValueError:
        return None


def host_only_test_targets(source_root, targets, changed_paths=None, graph=None, graph_changed=None):
    selected = []
    for target in targets.values():
        if target.get("type") != "EXECUTABLE":
            continue
        target_source_dir = (source_root / target["paths"]["source"]).resolve()
        try:
            target_source_dir.relative_to(source_root / "core" / "libs")
        except ValueError:
            continue
        sources = [source_root / source["path"] for source in target.get("sources", []) if not source.get("isGenerated")]
        if not any("tests" in source.parts and source.is_relative_to(source_root / "core" / "libs") for source in sources):
            continue
        if links_guest_libc(target) or links_product_library(target, targets, source_root):
            continue
        selected.append(target["name"])
    selected = sorted(set(selected), key=str.casefold)
    if changed_paths is None:
        return selected
    return changed_test_targets(source_root, targets, selected, changed_paths, HOST_BROAD_CHANGES, graph, graph_changed)


def local_build_change(path):
    return path.startswith("core/libs/") and (path.endswith(HEADERS) or Path(path).name == "CMakeLists.txt")


@functools.lru_cache(maxsize=None)
def graph_key(path):
    path = str(path).replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.casefold()


def read_graph(path, changed_since=None):
    if path is None:
        return None, None
    try:
        if changed_since is None:
            raise ValueError("no list of the paths changed since the commit it was written from")
        graph = load_graph(path)
        since = read_changed_paths(changed_since)
    except (OSError, ValueError) as error:
        print(f"Test graph not used; header and CMake changes select every test: {error}")
        return None, None
    return graph, since


def own_sources(source_root, target):
    return {
        graph_key(relative)
        for item in target.get("sources", []) if not item.get("isGenerated")
        for relative in [source_relative_path(source_root, source_root / item["path"])]
        if relative is not None
    }


def graph_test_targets(source_root, targets, selected, changed, graph, since):
    if graph is None or since is None:
        return None
    paths = {graph_key(path) for path in [*changed, *since] if path}
    if any(BUILD_FILES.search(path) for path in paths):
        return None
    closures = collections.defaultdict(set)
    for output, files in graph["closures"].items():
        closures[graph_key(output)].update(map(graph_key, files))
    known = set().union(*closures.values())
    for test in graph["tests"].values():
        known.update(map(graph_key, test["registered"] + test["arguments"]))
    names = {path.rpartition("/")[2] for path in known}
    candidates = set(selected)
    sources = collections.defaultdict(set)
    scopes = collections.defaultdict(set)
    others = set()
    for target in targets.values():
        name = target.get("name")
        if name not in candidates:
            others |= own_sources(source_root, target)
            continue
        sources[name] |= own_sources(source_root, target)
        for artifact in target.get("artifacts", []):
            scopes[name] |= closures.get(graph_key(artifact.get("path", "")), set())
    exclusive = set().union(*sources.values()) - others
    for path in paths - known:
        if (path.endswith(INCLUDED) or path.rpartition("/")[2] in names
                or path.endswith(COMPILED) and path not in exclusive):
            return None
    return {
        name for name in candidates
        if not any(path.endswith(HEADERS) for path in scopes[name])
        or not {path for path in sources[name] if path.endswith(COMPILED)} <= scopes[name]
        or scopes[name] & paths
    }


def changed_test_targets(source_root, targets, selected, changed_paths, broad_changes, graph=None, graph_changed=None):
    changed = {Path(path).as_posix() for path in changed_paths if path}
    if any(path in broad_changes for path in changed):
        return selected
    affected = set()
    if any(local_build_change(path) for path in changed):
        affected = graph_test_targets(source_root, targets, selected, changed, graph, graph_changed)
        if affected is None:
            return selected
    changed_source = {
        relative
        for target in targets.values()
        if target.get("name") in selected
        for item in target.get("sources", []) if not item.get("isGenerated")
        for relative in [source_relative_path(source_root, source_root / item["path"])]
        if relative is not None
    }
    matched_sources = changed & changed_source
    if matched_sources or affected:
        return [
            target["name"] for target in targets.values()
            if target.get("name") in selected
            and (target["name"] in affected or any(
                source_relative_path(source_root, source_root / item["path"]) in matched_sources
                for item in target.get("sources", []) if not item.get("isGenerated")
            ))
        ]
    return []


def main(argv=None):
    parser = argparse.ArgumentParser(description="build core/libs test executables that do not link guest libc")
    parser.add_argument("build_dir", type=Path)
    parser.add_argument("changed_paths", nargs="?", type=Path)
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--graph", type=Path, help="test graph written by select_tests.py dump")
    parser.add_argument("--graph-changed", type=Path, help="paths changed since the commit the test graph was written from")
    args = parser.parse_args(argv)
    try:
        source_root, targets = read_reply(Path(os.path.realpath(args.build_dir)))
        changed_paths = args.changed_paths.read_text(encoding="utf-8").splitlines() if args.changed_paths else None
        graph, graph_changed = read_graph(args.graph, args.graph_changed) if changed_paths is not None else (None, None)
        selected = host_only_test_targets(source_root, targets, changed_paths, graph, graph_changed)
        if not selected:
            print("No changed host-only core/libs test executables require a link check")
            return 0
        print("Host-only core/libs test executables:")
        print("\n".join(selected))
        if not args.list_only:
            subprocess.run(["cmake", "--build", str(args.build_dir), "--parallel", "3", "--target", *selected], check=True)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"{parser.prog}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
