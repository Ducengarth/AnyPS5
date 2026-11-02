import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_mingw_host_only_tests


class HostOnlyTargets(unittest.TestCase):
    def target(self, name, target_type="EXECUTABLE", source="core/libs/tests/HostOnly.cpp", dependencies=(), fragment=""):
        return {
            "artifacts": [{"path": f"tests/{name}.exe"}],
            "dependencies": [{"id": dependency} for dependency in dependencies],
            "id": name,
            "link": {"commandFragments": [{"fragment": fragment}] if fragment else []},
            "name": name,
            "paths": {"build": "core/libs", "source": "core/libs"},
            "sources": [{"path": source}],
            "type": target_type,
        }

    def fixture(self, targets):
        directory = Path(self.temporary.name)
        reply = directory / "build" / ".cmake" / "api" / "v1" / "reply"
        reply.mkdir(parents=True)
        entries = []
        for target in targets:
            filename = f"target-{target['name']}.json"
            (reply / filename).write_text(json.dumps(target), encoding="utf-8")
            entries.append({"id": target["id"], "jsonFile": filename, "name": target["name"]})
        model = {"configurations": [{"targets": entries}], "paths": {"source": str(directory)}}
        (reply / "codemodel.json").write_text(json.dumps(model), encoding="utf-8")
        index = {"reply": {"codemodel-v2": {"jsonFile": "codemodel.json"}}}
        (reply / "index-0001.json").write_text(json.dumps(index), encoding="utf-8")
        return directory / "build"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temporary.cleanup()

    def test_selects_core_libs_test_executables_without_guest_libc(self):
        host = self.target("host_tests")
        guest = self.target("guest_tests", dependencies=("libc-id",),
                             fragment="C:/build/core/libs/prx/libc/libc.dll.a")
        libc = self.target("libc", target_type="SHARED_LIBRARY")
        libc["id"] = "libc-id"
        other = self.target("outside_tests", source="core/relinker/tests/Outside.cpp")
        utility = self.target("not_a_test", source="core/libs/nid/src/main.cpp")
        build_dir = self.fixture([host, guest, libc, other, utility])
        source_root, targets = build_mingw_host_only_tests.read_reply(build_dir)
        self.assertEqual(build_mingw_host_only_tests.host_only_test_targets(source_root, targets), ["host_tests"])

    def test_excludes_mingw_libc_import_library_from_host_only_targets(self):
        guest = self.target("guest_mktime_tests", source="core/libs/tests/GuestMktime.cpp",
                            fragment="C:/build/core/libs/libs/implib/liblibc.dll.a")
        source_root = Path(self.temporary.name).resolve()
        self.assertEqual(build_mingw_host_only_tests.host_only_test_targets(source_root, {guest["id"]: guest}), [])

    def test_libc_link_does_not_accept_similarly_named_libraries(self):
        for fragment in ("/build/notliblibc.dll.a", "/build/liblibc.dll.a.extra", "/build/liblibc.prx"):
            with self.subTest(fragment=fragment):
                target = self.target("host_tests", fragment=fragment)
                self.assertFalse(build_mingw_host_only_tests.links_guest_libc(target))

    def test_excludes_a_transitive_guest_libc_dependency(self):
        helper = self.target("guest_helper", target_type="STATIC_LIBRARY", source="core/libs/tests/GuestHelper.cpp",
                             dependencies=("libc-id",))
        guest = self.target("guest_tests", dependencies=("helper-id",),
                             fragment="C:/build/core/libs/prx/libc/libc.dll.a")
        libc = self.target("libc", target_type="SHARED_LIBRARY")
        helper["id"] = "helper-id"
        libc["id"] = "libc-id"
        build_dir = self.fixture([guest, helper, libc])
        source_root, targets = build_mingw_host_only_tests.read_reply(build_dir)
        self.assertEqual(build_mingw_host_only_tests.host_only_test_targets(source_root, targets), [])

    def test_excludes_a_direct_libc_link_but_ignores_the_full_libraries_build_target(self):
        direct_file = self.target("direct_file_tests", fragment="/build/libs/libc.prx")
        all_libraries = self.target("all_libraries_tests", dependencies=("libs-id",))
        libraries = self.target("libs", target_type="UTILITY")
        libraries["id"] = "libs-id"
        build_dir = self.fixture([direct_file, all_libraries, libraries])
        source_root, targets = build_mingw_host_only_tests.read_reply(build_dir)
        self.assertEqual(build_mingw_host_only_tests.host_only_test_targets(source_root, targets), ["all_libraries_tests"])

    def test_selects_host_test_linking_root_declared_native_helper(self):
        helper = self.target("host_thread_local_test_library", target_type="SHARED_LIBRARY",
                             source="core/libs/tests/HostThreadLocalLibrary.cpp")
        helper["artifacts"] = [{"path": "core/libs/libhost_thread_local_test_library.dll.a"}]
        helper["id"] = "helper-id"
        host = self.target("host_thread_local_tests", dependencies=("helper-id",),
                           source="core/libs/tests/HostThreadLocal.cpp",
                           fragment="/build/core/libs/libhost_thread_local_test_library.dll.a")
        build_dir = self.fixture([helper, host])
        source_root, targets = build_mingw_host_only_tests.read_reply(build_dir)
        self.assertEqual(
            build_mingw_host_only_tests.host_only_test_targets(source_root, targets),
            ["host_thread_local_tests"],
        )

    def test_selects_host_test_linking_nested_test_directory_helper(self):
        helper = self.target("nested_helper", target_type="STATIC_LIBRARY",
                             source="core/libs/prx/custom/tests/NestedHelper.cpp")
        helper["paths"]["source"] = "core/libs/prx/custom"
        helper["artifacts"] = [{"path": "core/libs/prx/custom/libnested_helper.a"}]
        helper["id"] = "nested-helper-id"
        host = self.target("nested_helper_tests", dependencies=("nested-helper-id",),
                           source="core/libs/tests/NestedTests.cpp",
                           fragment="/build/core/libs/prx/custom/libnested_helper.a")
        build_dir = self.fixture([helper, host])
        source_root, targets = build_mingw_host_only_tests.read_reply(build_dir)
        self.assertEqual(
            build_mingw_host_only_tests.host_only_test_targets(source_root, targets),
            ["nested_helper_tests"],
        )

    def test_excludes_library_with_mixed_test_and_product_sources(self):
        mixed = self.target("mixed_lib", target_type="STATIC_LIBRARY",
                            source="core/libs/tests/Helper.cpp")
        mixed["sources"].append({"path": "core/libs/src/Product.cpp"})
        mixed["artifacts"] = [{"path": "core/libs/libmixed_lib.a"}]
        mixed["id"] = "mixed-id"
        host = self.target("mixed_dependent_tests", dependencies=("mixed-id",),
                           source="core/libs/tests/HostOnly.cpp",
                           fragment="/build/core/libs/libmixed_lib.a")
        build_dir = self.fixture([mixed, host])
        source_root, targets = build_mingw_host_only_tests.read_reply(build_dir)
        self.assertEqual(
            build_mingw_host_only_tests.host_only_test_targets(source_root, targets),
            [],
        )

    def test_excludes_host_test_when_helper_transitively_links_product_library(self):
        product = self.target("product_lib", target_type="STATIC_LIBRARY",
                              source="core/libs/utility/src/Utility.cpp")
        product["paths"]["source"] = "core/libs/utility"
        product["artifacts"] = [{"path": "core/libs/utility/libproduct_lib.a"}]
        product["id"] = "product-id"
        helper = self.target("test_helper", target_type="SHARED_LIBRARY",
                             source="core/libs/tests/Helper.cpp",
                             fragment="/build/core/libs/utility/libproduct_lib.a")
        helper["artifacts"] = [{"path": "core/libs/tests/libtest_helper.dll.a"}]
        helper["id"] = "helper-id"
        host = self.target("host_tests", dependencies=("helper-id",),
                           source="core/libs/tests/HostOnly.cpp",
                           fragment="/build/core/libs/tests/libtest_helper.dll.a")
        build_dir = self.fixture([product, helper, host])
        source_root, targets = build_mingw_host_only_tests.read_reply(build_dir)
        self.assertEqual(
            build_mingw_host_only_tests.host_only_test_targets(source_root, targets),
            [],
        )

    def test_excludes_host_test_when_helper_links_guest_libc_without_libc_target(self):
        helper = self.target("test_helper", target_type="SHARED_LIBRARY",
                             source="core/libs/tests/Helper.cpp",
                             fragment="C:/build/core/libs/prx/libc/libc.dll.a")
        helper["artifacts"] = [{"path": "core/libs/tests/libtest_helper.dll.a"}]
        helper["id"] = "helper-id"
        host = self.target("host_tests", dependencies=("helper-id",),
                           source="core/libs/tests/HostOnly.cpp",
                           fragment="/build/core/libs/tests/libtest_helper.dll.a")
        build_dir = self.fixture([helper, host])
        source_root, targets = build_mingw_host_only_tests.read_reply(build_dir)
        self.assertEqual(
            build_mingw_host_only_tests.host_only_test_targets(source_root, targets),
            [],
        )

    def test_ignores_helper_order_only_dependency_on_product_target(self):
        product = self.target("product_lib", target_type="STATIC_LIBRARY",
                              source="core/libs/utility/src/Utility.cpp")
        product["paths"]["source"] = "core/libs/utility"
        product["artifacts"] = [{"path": "core/libs/utility/libproduct_lib.a"}]
        product["id"] = "product-id"
        helper = self.target("test_helper", target_type="SHARED_LIBRARY",
                             source="core/libs/tests/Helper.cpp",
                             dependencies=("product-id",))
        helper["artifacts"] = [{"path": "core/libs/tests/libtest_helper.dll.a"}]
        helper["id"] = "helper-id"
        host = self.target("host_tests", dependencies=("helper-id",),
                           source="core/libs/tests/HostOnly.cpp",
                           fragment="/build/core/libs/tests/libtest_helper.dll.a")
        build_dir = self.fixture([product, helper, host])
        source_root, targets = build_mingw_host_only_tests.read_reply(build_dir)
        self.assertEqual(
            build_mingw_host_only_tests.host_only_test_targets(source_root, targets),
            ["host_tests"],
        )

    def test_safely_handles_finite_helper_link_cycle(self):
        helper_a = self.target("helper_a", target_type="SHARED_LIBRARY",
                               source="core/libs/tests/HelperA.cpp",
                               fragment="/build/core/libs/tests/libhelper_b.dll.a")
        helper_a["artifacts"] = [{"path": "core/libs/tests/libhelper_a.dll.a"}]
        helper_a["id"] = "helper-a-id"
        helper_b = self.target("helper_b", target_type="SHARED_LIBRARY",
                               source="core/libs/tests/HelperB.cpp",
                               fragment="/build/core/libs/tests/libhelper_a.dll.a")
        helper_b["artifacts"] = [{"path": "core/libs/tests/libhelper_b.dll.a"}]
        helper_b["id"] = "helper-b-id"
        host = self.target("cycle_tests", dependencies=("helper-a-id",),
                           source="core/libs/tests/HostOnly.cpp",
                           fragment="/build/core/libs/tests/libhelper_a.dll.a")
        build_dir = self.fixture([helper_a, helper_b, host])
        source_root, targets = build_mingw_host_only_tests.read_reply(build_dir)
        self.assertEqual(
            build_mingw_host_only_tests.host_only_test_targets(source_root, targets),
            ["cycle_tests"],
        )

    def test_falls_back_to_declaration_directory_when_library_has_no_sources(self):
        test_dir_lib = self.target("test_dir_lib", target_type="STATIC_LIBRARY")
        test_dir_lib["paths"]["source"] = "core/libs/tests"
        test_dir_lib["sources"] = []
        test_dir_lib["artifacts"] = [{"path": "core/libs/tests/libtest_dir_lib.a"}]
        test_dir_lib["id"] = "test-dir-lib-id"
        parent_dir_lib = self.target("parent_dir_lib", target_type="STATIC_LIBRARY")
        parent_dir_lib["paths"]["source"] = "core/libs"
        parent_dir_lib["sources"] = []
        parent_dir_lib["artifacts"] = [{"path": "core/libs/libparent_dir_lib.a"}]
        parent_dir_lib["id"] = "parent-dir-lib-id"
        host_ok = self.target("host_ok", dependencies=("test-dir-lib-id",),
                              source="core/libs/tests/HostOnly.cpp",
                              fragment="/build/core/libs/tests/libtest_dir_lib.a")
        host_excluded = self.target("host_excluded", dependencies=("parent-dir-lib-id",),
                                    source="core/libs/tests/HostOnly.cpp",
                                    fragment="/build/core/libs/libparent_dir_lib.a")
        build_dir = self.fixture([test_dir_lib, parent_dir_lib, host_ok, host_excluded])
        source_root, targets = build_mingw_host_only_tests.read_reply(build_dir)
        self.assertEqual(
            build_mingw_host_only_tests.host_only_test_targets(source_root, targets),
            ["host_ok"],
        )

    def test_excludes_product_library_dependencies_but_keeps_test_helpers(self):
        product = self.target("product_lib", target_type="STATIC_LIBRARY", source="core/libs/utility/src/Utility.cpp")
        product["artifacts"] = [{"path": "core/libs/utility/libproduct_lib.a"}]
        product["id"] = "product-id"
        production_dependent = self.target("production_dependent", dependencies=("product-id",),
                                            fragment="/build/core/libs/utility/libproduct_lib.a")
        helper = self.target("test_helper", target_type="STATIC_LIBRARY", source="core/libs/tests/GuestHelper.cpp")
        helper["paths"]["source"] = "core/libs/tests"
        helper["id"] = "helper-id"
        helper_dependent = self.target("helper_dependent", dependencies=("helper-id",), source="core/libs/tests/HostOnly.cpp")
        external = self.target("external", target_type="STATIC_LIBRARY", source="3rdparty/sdl/src/SDL.cpp")
        external["paths"]["source"] = "3rdparty/sdl/src"
        external["id"] = "external-id"
        external_dependent = self.target("external_dependent", dependencies=("external-id",))
        build_dir = self.fixture([product, production_dependent, helper, helper_dependent, external, external_dependent])
        source_root, targets = build_mingw_host_only_tests.read_reply(build_dir)
        self.assertEqual(build_mingw_host_only_tests.host_only_test_targets(source_root, targets), ["external_dependent", "helper_dependent"])

    def test_ignores_build_order_dependencies_that_are_not_linked(self):
        host = self.target(
            "guest_sigvec_modules_tests",
            dependencies=("libc-id", "kernel-id", "internal-id", "patcher-id"),
        )
        libc = self.target("libc", target_type="SHARED_LIBRARY", source="core/libs/libc/src/libc.cpp")
        kernel = self.target("libkernel", target_type="SHARED_LIBRARY", source="core/libs/libkernel/src/kernel.cpp")
        internal = self.target("libSceLibcInternal", target_type="SHARED_LIBRARY", source="core/libs/internal/src/internal.cpp")
        patcher = self.target("nid_patcher", target_type="STATIC_LIBRARY", source="core/libs/nid_patcher/src/patcher.cpp")
        libc["id"], kernel["id"], internal["id"], patcher["id"] = "libc-id", "kernel-id", "internal-id", "patcher-id"
        build_dir = self.fixture([host, libc, kernel, internal, patcher])
        source_root, targets = build_mingw_host_only_tests.read_reply(build_dir)
        self.assertEqual(
            build_mingw_host_only_tests.host_only_test_targets(source_root, targets),
            ["guest_sigvec_modules_tests"],
        )

    def test_selects_only_changed_host_test_sources(self):
        first = self.target("first_tests")
        second = self.target("second_tests", source="core/libs/tests/Second.cpp")
        build_dir = self.fixture([first, second])
        source_root, targets = build_mingw_host_only_tests.read_reply(build_dir)
        selected = build_mingw_host_only_tests.host_only_test_targets(
            source_root, targets, ["core/libs/tests/Second.cpp"]
        )
        self.assertEqual(selected, ["second_tests"])

    def test_cmake_or_header_changes_check_all_host_targets(self):
        first = self.target("first_tests")
        second = self.target("second_tests", source="core/libs/tests/Second.cpp")
        build_dir = self.fixture([first, second])
        source_root, targets = build_mingw_host_only_tests.read_reply(build_dir)
        self.assertEqual(
            build_mingw_host_only_tests.host_only_test_targets(
                source_root, targets, ["core/libs/CMakeLists.txt"]
            ),
            ["first_tests", "second_tests"],
        )
        self.assertEqual(
            build_mingw_host_only_tests.host_only_test_targets(
                source_root, targets, ["core/libs/tests/HostOnly.hpp"]
            ),
            ["first_tests", "second_tests"],
        )

    def test_header_listed_as_a_target_source_still_selects_all_host_targets(self):
        first = self.target("first_tests")
        second = self.target("second_tests", source="core/libs/tests/Second.cpp")
        second["sources"].append({"path": "core/libs/tests/Shared.hpp"})
        build_dir = self.fixture([first, second])
        source_root, targets = build_mingw_host_only_tests.read_reply(build_dir)
        self.assertEqual(
            build_mingw_host_only_tests.host_only_test_targets(
                source_root, targets, ["core/libs/tests/Shared.hpp"]
            ),
            ["first_tests", "second_tests"],
        )

    def test_fails_when_cmake_did_not_write_a_file_api_index(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "no CMake File API index"):
                build_mingw_host_only_tests.read_reply(Path(directory))

    def test_symbolic_link_loop_build_directory_is_reported_cleanly(self):
        root = Path(self.temporary.name)
        loop = root / "build"
        try:
            loop.symlink_to(root / "other")
            (root / "other").symlink_to(loop)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"symbolic links are unavailable: {error}")
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(build_mingw_host_only_tests.main([str(loop), "--list-only"]), 1)
        self.assertIn("no CMake File API index", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_wrong_shaped_file_api_documents_are_reported_cleanly(self):
        cases = (
            ("index", [], "CMake File API index"),
            ("index", {"reply": []}, "CMake File API reply"),
            ("index", {"reply": {"codemodel-v2": []}}, "CMake codemodel reference"),
            ("model", [], "CMake codemodel"),
            ("model", {"configurations": [[]], "paths": {"source": "."}}, "CMake configuration"),
            ("model", {"configurations": [{"targets": [[]]}], "paths": {"source": "."}}, "CMake target reference"),
            ("target", [], "CMake target"),
        )
        for location, document, expected in cases:
            with self.subTest(location=location, expected=expected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                reply = root / "build" / ".cmake" / "api" / "v1" / "reply"
                reply.mkdir(parents=True)
                target = self.target("host_tests")
                target_name = "target-host_tests.json"
                (reply / target_name).write_text(json.dumps(target), encoding="utf-8")
                model = {"configurations": [{"targets": [{"id": target["id"], "jsonFile": target_name}]}],
                         "paths": {"source": str(root)}}
                (reply / "codemodel.json").write_text(json.dumps(model), encoding="utf-8")
                index = {"reply": {"codemodel-v2": {"jsonFile": "codemodel.json"}}}
                (reply / "index-0001.json").write_text(json.dumps(index), encoding="utf-8")
                path = {"index": reply / "index-0001.json", "model": reply / "codemodel.json",
                        "target": reply / target_name}[location]
                path.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, expected):
                    build_mingw_host_only_tests.read_reply(root / "build")
                errors = io.StringIO()
                with contextlib.redirect_stderr(errors):
                    self.assertEqual(build_mingw_host_only_tests.main([str(root / "build"), "--list-only"]), 1)
                self.assertIn(expected, errors.getvalue())
                self.assertNotIn("Traceback", errors.getvalue())

    def test_wrong_shaped_target_members_are_reported_cleanly(self):
        cases = (
            ({"name": []}, "CMake target name"),
            ({"link": []}, "CMake target link"),
            ({"link": {"commandFragments": ["bad"]}}, "CMake link command fragments"),
            ({"artifacts": ["bad"]}, "CMake target artifacts"),
            ({"sources": ["bad"]}, "CMake target sources"),
        )
        for changes, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                reply = root / "build" / ".cmake" / "api" / "v1" / "reply"
                reply.mkdir(parents=True)
                target = self.target("host_tests")
                target.update(changes)
                (reply / "target.json").write_text(json.dumps(target), encoding="utf-8")
                model = {"configurations": [{"targets": [{"id": "host_tests", "jsonFile": "target.json"}]}],
                         "paths": {"source": str(root)}}
                (reply / "codemodel.json").write_text(json.dumps(model), encoding="utf-8")
                index = {"reply": {"codemodel-v2": {"jsonFile": "codemodel.json"}}}
                (reply / "index-0001.json").write_text(json.dumps(index), encoding="utf-8")
                errors = io.StringIO()
                with contextlib.redirect_stderr(errors):
                    self.assertEqual(build_mingw_host_only_tests.main([str(root / "build"), "--list-only"]), 1)
                self.assertIn(expected, errors.getvalue())
                self.assertNotIn("Traceback", errors.getvalue())


    def test_library_filename_substring_does_not_exclude_a_host_test(self):
        product = self.target("product_lib", target_type="STATIC_LIBRARY", source="core/libs/utility/src/Utility.cpp")
        product["artifacts"] = [{"path": "core/libs/utility/libproduct_lib.a"}]
        host = self.target("host_tests", fragment="C:/build/external/libprefix_libproduct_lib.a")
        guest = self.target("product_tests", fragment='"C:/build/core/libs/utility/libproduct_lib.a"')
        source_root, targets = build_mingw_host_only_tests.read_reply(self.fixture([product, host, guest]))
        self.assertEqual(build_mingw_host_only_tests.host_only_test_targets(source_root, targets), ["host_tests"])

    def assert_exact_file_argument_links_product(self, fragment):
        product = self.target("product_lib", target_type="STATIC_LIBRARY", source="core/libs/utility/src/Utility.cpp")
        product["artifacts"] = [{"path": "core/libs/utility/libproduct_lib.a"}]
        guest = self.target("product_tests", fragment=fragment)
        source_root, targets = build_mingw_host_only_tests.read_reply(self.fixture([product, guest]))
        self.assertEqual(build_mingw_host_only_tests.host_only_test_targets(source_root, targets), [])

    def test_gnu_exact_file_linker_argument_links_a_product_library(self):
        self.assert_exact_file_argument_links_product("-l:libproduct_lib.a")

    def test_gnu_exact_file_linker_argument_through_wl_links_a_product_library(self):
        self.assert_exact_file_argument_links_product("-Wl,-l:libproduct_lib.a")


ALL_TARGETS = ["first_tests", "second_tests", "third_tests"]


class GraphSelection(unittest.TestCase):
    target = HostOnlyTargets.target
    fixture = HostOnlyTargets.fixture
    setUp = HostOnlyTargets.setUp
    tearDown = HostOnlyTargets.tearDown

    def targets(self):
        if not hasattr(self, "reply"):
            self.reply = build_mingw_host_only_tests.read_reply(self.fixture([
                self.target("first_tests", source="core/libs/tests/First.cpp"),
                self.target("second_tests", source="core/libs/tests/Second.cpp"),
                self.target("third_tests", source="core/libs/tests/Third.cpp"),
            ]))
        return self.reply

    def graph(self, **closures):
        files = {
            "first_tests": ["core/libs/tests/First.cpp", "core/libs/tests/First.hpp", "core/libs/tests/Shared.hpp"],
            "second_tests": ["core/libs/tests/Second.cpp", "core/libs/tests/Second.hpp", "core/libs/tests/Shared.hpp",
                             "core/libs/tests/helper/Helper.cpp", "core/libs/tests/helper/include/Helper.hpp",
                             "core/libs/tests/helper/include/Inner.inl"],
            "third_tests": ["core/libs/tests/Third.cpp", "core/libs/tests/Third.hh"],
        }
        files.update(closures)
        files = {name: paths for name, paths in files.items() if paths is not None}
        tests = {name: {"registered": ["core/libs/CMakeLists.txt"], "arguments": [], "sets": [], "requires": [],
                        "depends": [], "target": f"tests/{name}.exe"} for name in files}
        return {"tests": tests, "closures": {f"tests/{name}.exe": paths for name, paths in files.items()}}

    def select(self, changed, graph=None, since=None, use_graph=True):
        source_root, targets = self.targets()
        if use_graph and graph is None:
            graph = self.graph()
        return build_mingw_host_only_tests.host_only_test_targets(source_root, targets, changed, graph, since)

    def write_graph(self, document):
        path = Path(self.temporary.name) / "test-graph.json"
        path.write_text(document if isinstance(document, str) else json.dumps(document), encoding="utf-8")
        return path

    def test_a_header_of_one_target_selects_only_that_target(self):
        self.assertEqual(self.select(["core/libs/tests/First.hpp"]), ["first_tests"])

    def test_a_shared_header_selects_every_target_including_it(self):
        self.assertEqual(self.select(["core/libs/tests/Shared.hpp"]), ["first_tests", "second_tests"])

    def test_a_header_reached_through_a_linked_helper_selects_its_dependents(self):
        self.assertEqual(self.select(["core/libs/tests/helper/include/Inner.inl"]), ["second_tests"])

    def test_a_header_the_graph_does_not_know_selects_every_target(self):
        self.assertEqual(self.select(["core/libs/tests/New.hpp"]), ALL_TARGETS)

    def test_no_graph_keeps_the_broad_selection(self):
        self.assertEqual(self.select(["core/libs/tests/First.hpp"], use_graph=False), ALL_TARGETS)

    def test_a_known_header_no_target_includes_selects_nothing(self):
        graph = self.graph()
        graph["closures"]["tests/other.exe"] = ["core/libs/other/Other.cpp", "core/libs/other/Other.hpp"]
        self.assertEqual(self.select(["core/libs/other/Other.hpp"], graph), [])

    def test_several_headers_select_the_union_of_their_dependents(self):
        self.assertEqual(self.select(["core/libs/tests/First.hpp", "core/libs/tests/Third.hh"]),
                         ["first_tests", "third_tests"])

    def test_a_header_with_a_build_wide_change_selects_every_target(self):
        for path in ("CMakeLists.txt", ".github/workflows/build.yml", "tools/build_mingw_host_only_tests.py",
                     "cmake/Toolchain.cmake", "3rdparty/zlib/zlib.h", "tools/select_tests.py", "core/CMakeLists.txt"):
            with self.subTest(path=path):
                self.assertEqual(self.select(["core/libs/tests/First.hpp", path]), ALL_TARGETS)

    def test_a_header_with_a_test_source_selects_both_targets(self):
        self.assertEqual(self.select(["core/libs/tests/First.hpp", "core/libs/tests/Third.cpp"]),
                         ["first_tests", "third_tests"])

    def test_a_header_with_a_known_library_source_selects_its_dependents(self):
        self.assertEqual(self.select(["core/libs/tests/First.hpp", "core/libs/tests/helper/Helper.cpp"]),
                         ["first_tests", "second_tests"])

    def test_a_header_with_an_unknown_library_source_selects_every_target(self):
        self.assertEqual(self.select(["core/libs/tests/First.hpp", "core/libs/tests/helper/New.cpp"]), ALL_TARGETS)

    def test_a_header_with_an_unrelated_file_selects_its_dependents(self):
        self.assertEqual(self.select(["core/libs/tests/First.hpp", "docs/dev/BUILD.md", "core/libs/prx/x/notes.md"]),
                         ["first_tests"])

    def test_cmake_changes_keep_the_broad_selection(self):
        for changed in (["core/libs/tests/CMakeLists.txt"], ["core/libs/CMakeLists.txt"],
                        ["core/libs/tests/First.hpp", "core/libs/tests/helper/CMakeLists.txt"],
                        ["core/libs/tests/First.hpp", "core/libs/prx/libSceFontFt/FontFtLibrary.cmake"]):
            with self.subTest(changed=changed):
                self.assertEqual(self.select(changed), ALL_TARGETS)

    def test_changes_since_the_graph_commit_widen_the_selection(self):
        self.assertEqual(self.select(["core/libs/tests/First.hpp"],
                                     since=["core/libs/tests/First.hpp", "core/libs/tests/Third.cpp"]),
                         ["first_tests", "third_tests"])
        self.assertEqual(self.select(["core/libs/tests/First.hpp"], since=["core/libs/tests/helper/include/Helper.hpp"]),
                         ["first_tests", "second_tests"])

    def test_build_file_changes_since_the_graph_commit_select_every_target(self):
        for path in ("core/libs/prx/libScePad/CMakeLists.txt", "3rdparty/zlib", ".gitmodules", "core/libs/tests/Gone.hpp"):
            with self.subTest(path=path):
                self.assertEqual(self.select(["core/libs/tests/First.hpp"], since=[path]), ALL_TARGETS)

    def test_a_target_missing_from_the_graph_is_selected(self):
        self.assertEqual(self.select(["core/libs/tests/First.hpp"], self.graph(third_tests=None)),
                         ["first_tests", "third_tests"])

    def test_a_target_without_recorded_header_dependencies_is_selected(self):
        self.assertEqual(self.select(["core/libs/tests/First.hpp"], self.graph(third_tests=["core/libs/tests/Third.cpp"])),
                         ["first_tests", "third_tests"])

    def test_a_target_whose_sources_moved_since_the_graph_is_selected(self):
        graph = self.graph(third_tests=["core/libs/tests/OldThird.cpp", "core/libs/tests/Third.hh"])
        self.assertEqual(self.select(["core/libs/tests/First.hpp"], graph), ["first_tests", "third_tests"])

    def test_source_only_changes_select_the_same_targets_with_or_without_a_graph(self):
        for changed in (["core/libs/tests/Second.cpp"], ["core/libs/prx/libc/src/FloatBuiltins.cpp"],
                        ["core/relinker/codegen/src/Amd64OnlyConverter.cpp"], ["core/common/Shared.hpp"]):
            with self.subTest(changed=changed):
                self.assertEqual(self.select(changed), self.select(changed, use_graph=False))

    def test_windows_separators_and_case_in_the_graph_still_match(self):
        graph = self.graph()
        graph["closures"] = {key.upper().replace("/", "\\"): [path.replace("/", "\\").upper() for path in files]
                             for key, files in graph["closures"].items()}
        graph["tests"] = {name: dict(test, target=test["target"].upper().replace("/", "\\"))
                          for name, test in graph["tests"].items()}
        self.assertEqual(self.select(["core/libs/tests/First.hpp"], graph), ["first_tests"])
        self.assertEqual(self.select(["core/libs/TESTS/first.hpp"]), ["first_tests"])

    def test_read_graph_rejects_missing_and_malformed_graphs(self):
        documents = {
            "missing": None,
            "invalid json": "{",
            "wrong shape": {"tests": [], "closures": {}},
            "missing closure": {"tests": {"t": {"registered": [], "arguments": [], "sets": [], "requires": [],
                                                "depends": [], "target": "tests/t.exe"}}, "closures": {}},
        }
        for name, document in documents.items():
            with self.subTest(name=name):
                path = Path(self.temporary.name) / "absent.json" if document is None else self.write_graph(document)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(build_mingw_host_only_tests.read_graph(path), (None, None))
                self.assertIn("Test graph not used", output.getvalue())

    def test_read_graph_rejects_an_unreadable_changed_since_list(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build_mingw_host_only_tests.read_graph(
                self.write_graph(self.graph()), Path(self.temporary.name) / "absent.txt"), (None, None))

    def test_read_graph_reads_newline_and_nul_separated_changes(self):
        for content in (b"core/libs/a.hpp\ncore/libs/b.cpp\n", b"core/libs/a.hpp\0core/libs/b.cpp\0"):
            with self.subTest(content=content):
                since = Path(self.temporary.name) / "since.txt"
                since.write_bytes(content)
                graph, paths = build_mingw_host_only_tests.read_graph(self.write_graph(self.graph()), since)
                self.assertEqual(graph, self.graph())
                self.assertEqual(paths, ["core/libs/a.hpp", "core/libs/b.cpp"])

    def run_main(self, *options, changed=("core/libs/tests/First.hpp",)):
        self.targets()
        listed = Path(self.temporary.name) / "changed.txt"
        listed.write_text("\n".join(changed), encoding="utf-8")
        output = io.StringIO()
        build = Path(self.temporary.name) / "build"
        with contextlib.redirect_stdout(output):
            code = build_mingw_host_only_tests.main([str(build), str(listed), "--list-only", *map(str, options)])
        self.assertEqual(code, 0)
        return output.getvalue().splitlines()

    def test_main_selects_from_the_graph(self):
        since = Path(self.temporary.name) / "graph-changed.txt"
        since.write_text("core/libs/tests/First.hpp\n", encoding="utf-8")
        graph = self.write_graph(self.graph())
        self.assertEqual(self.run_main("--graph", graph, "--graph-changed", since),
                         ["Host-only core/libs test executables:", "first_tests"])

    def test_main_without_a_usable_graph_checks_every_target(self):
        lines = self.run_main("--graph", Path(self.temporary.name) / "test-graph.json")
        self.assertTrue(lines[0].startswith("Test graph not used"))
        self.assertEqual(lines[1:], ["Host-only core/libs test executables:", *ALL_TARGETS])


@unittest.skipUnless(shutil.which("cmake") and shutil.which("ninja") and (shutil.which("gcc") or shutil.which("cc")),
                     "needs cmake, ninja and a C compiler")
class GraphSelectionFromARealBuild(unittest.TestCase):
    PROJECT = {
        "CMakeLists.txt": "cmake_minimum_required(VERSION 3.20)\nproject(mini C)\nenable_testing()\n"
                          "add_subdirectory(core/libs)\n",
        "core/libs/CMakeLists.txt": "add_library(test_helper STATIC tests/helper/Helper.c)\n"
                                    "target_include_directories(test_helper PUBLIC tests/helper/include)\n"
                                    "add_executable(first_tests tests/First.c)\n"
                                    "target_link_libraries(first_tests PRIVATE test_helper)\n"
                                    "add_executable(second_tests tests/Second.c)\n"
                                    "add_test(NAME first COMMAND first_tests)\nadd_test(NAME second COMMAND second_tests)\n",
        "core/libs/tests/helper/Helper.c": "#include \"Helper.h\"\nint helper(void) { return INNER; }\n",
        "core/libs/tests/helper/include/Helper.h": "#include \"Inner.h\"\nint helper(void);\n",
        "core/libs/tests/helper/include/Inner.h": "#define INNER 0\n",
        "core/libs/tests/First.c": "int helper(void);\nint main(void) { return helper(); }\n",
        "core/libs/tests/Second.c": "#include \"Second.h\"\nint main(void) { return SECOND; }\n",
        "core/libs/tests/Second.h": "#define SECOND 0\n",
    }

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        cls.source, cls.build, cls.graph_path = root / "source", root / "build", root / "test-graph.json"
        for name, content in cls.PROJECT.items():
            (cls.source / name).parent.mkdir(parents=True, exist_ok=True)
            (cls.source / name).write_text(content, encoding="utf-8")
        query = cls.build / ".cmake" / "api" / "v1" / "query"
        query.mkdir(parents=True)
        (query / "codemodel-v2").touch()
        compiler = shutil.which("gcc") or shutil.which("cc")
        subprocess.run(["cmake", "-S", str(cls.source), "-B", str(cls.build), "-G", "Ninja", f"-DCMAKE_C_COMPILER={compiler}"],
                       check=True, capture_output=True)
        subprocess.run(["cmake", "--build", str(cls.build)], check=True, capture_output=True)
        tool = Path(__file__).resolve().parents[1] / "select_tests.py"
        subprocess.run([sys.executable, str(tool), "dump", str(cls.build), str(cls.graph_path)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def select(self, changed):
        source_root, targets = build_mingw_host_only_tests.read_reply(Path(os.path.realpath(self.build)))
        with contextlib.redirect_stdout(io.StringIO()):
            graph, since = build_mingw_host_only_tests.read_graph(self.graph_path)
        self.assertIsNotNone(graph)
        return build_mingw_host_only_tests.host_only_test_targets(source_root, targets, changed, graph, since)

    def test_a_header_included_only_by_a_linked_helper_selects_its_dependent(self):
        self.assertEqual(self.select(["core/libs/tests/helper/include/Inner.h"]), ["first_tests"])

    def test_a_header_of_one_executable_selects_only_it(self):
        self.assertEqual(self.select(["core/libs/tests/Second.h"]), ["second_tests"])

    def test_an_unknown_header_selects_every_executable(self):
        self.assertEqual(self.select(["core/libs/tests/Third.h"]), ["first_tests", "second_tests"])


class WindowsPullRequestGateTests(unittest.TestCase):
    def test_pull_requests_select_host_and_guest_tests_from_the_test_graph(self):
        workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/build.yml").read_text(encoding="utf-8")
        windows = workflow.split("\n  windows:\n", 1)[1].split("\n  edge-watch:\n", 1)[0]
        restore = windows.split("- id: pr-graph\n", 1)[1].split("\n      - ", 1)[0]
        self.assertIn("if: github.event_name == 'pull_request'", restore)
        self.assertIn("key: test-graph-${{ runner.os }}-${{ needs.eligibility.outputs.sha }}", restore)
        self.assertIn('git diff --no-renames --name-only "$graph" HEAD > graph-changed.txt', windows)
        for script in ("build_mingw_host_only_tests.py", "build_changed_guest_tests.py"):
            with self.subTest(script=script):
                self.assertIn(f"python tools/{script} build changed.txt --graph test-graph.json "
                              "--graph-changed graph-changed.txt", windows)

    def test_build_wide_inputs_start_the_windows_pull_request_job(self):
        workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/build.yml").read_text(encoding="utf-8")
        gate = workflow.split("mingwHostTests: ", 1)[1].split("),", 1)[0]
        for item in ("'core/CMakeLists.txt'", "'cmake/'", "'3rdparty/'", "'.gitmodules'",
                     "'tools/select_tests.py'", "'tools/configure_seed.py'"):
            with self.subTest(item=item):
                self.assertIn(item, gate)


if __name__ == "__main__":
    unittest.main()
