"""Host generator publication contracts; no guest compilation is implied."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('route_generator', Path(__file__).resolve().parents[1]/'scripts/generate-routes.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class RoutePublication(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        routes = self.root/'src/routes/rest/health'
        routes.mkdir(parents=True)
        (routes/'get.ores').write_text('route fixture')
        self.patch = patch.object(generator, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_leaf_symlink_is_replaced_without_modifying_target(self):
        generated = self.root/'src/generated'
        generated.mkdir()
        target = self.root/'important.txt'
        target.write_text('preserve me')
        for name in ('routes.ores','config.ores'):
            (generated/name).symlink_to(target)
        generator.generate(3000, self.root/'data')
        self.assertEqual(target.read_text(),'preserve me')
        self.assertFalse((generated/'routes.ores').is_symlink())
        self.assertIn('GET /health',(generated/'routes.ores').read_text())
        self.assertIn('3000',(generated/'config.ores').read_text())

    def test_failed_replace_preserves_previous_output_and_cleans_temporary(self):
        destination = self.root/'routes.ores'
        destination.write_text('previous valid source')
        with patch.object(generator.os,'replace',side_effect=OSError('fixture disk failure')):
            with self.assertRaises(OSError):
                generator.publish(destination,'new source')
        self.assertEqual(destination.read_text(),'previous valid source')
        self.assertEqual(list(self.root.glob('.routes-*')),[])

    def test_symlinked_route_root_rejected(self):
        route_root = self.root/'src/routes/rest'
        external = self.root/'external-routes'
        route_root.rename(external)
        route_root.symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError):
            generator.generate(3000, self.root/'data')
        self.assertFalse((self.root/'src/generated/routes.ores').exists())

    def test_symlinked_output_directory_rejected(self):
        target = self.root/'elsewhere'
        target.mkdir()
        (self.root/'src/generated').symlink_to(target,target_is_directory=True)
        with self.assertRaises(ValueError):
            generator.generate(3000,self.root/'data')
        self.assertEqual(list(target.iterdir()),[])

    def test_invalid_ports_do_not_publish_any_source(self):
        for bad in (0, -1, 65536, 2**63):
            with self.subTest(port=bad), self.assertRaises(ValueError):
                generator.generate(bad, self.root/'data')
            self.assertFalse((self.root/'src/generated/routes.ores').exists())
            self.assertFalse((self.root/'src/generated/config.ores').exists())

    def test_unsupported_method_is_not_registered_or_published(self):
        trace = self.root/'src/routes/rest/health/trace.ores'
        trace.write_text('route fixture')
        with self.assertRaisesRegex(ValueError, 'unsupported method filename'):
            generator.generate(3000, self.root/'data')
        self.assertFalse((self.root/'src/generated/routes.ores').exists())

    def test_nonliteral_route_segment_is_not_published(self):
        path = self.root/'src/routes/rest/bad segment'
        path.mkdir()
        (path/'get.ores').write_text('route fixture')
        with self.assertRaisesRegex(ValueError, 'literal URL segments'):
            generator.generate(3000, self.root/'data')
        self.assertFalse((self.root/'src/generated/routes.ores').exists())

    def test_symlinked_route_leaf_is_not_traversed(self):
        route = self.root/'src/routes/rest/health/post.ores'
        route.symlink_to(self.root/'src/routes/rest/health/get.ores')
        with self.assertRaises(ValueError):
            generator.generate(3000, self.root/'data')
        self.assertFalse((self.root/'src/generated/routes.ores').exists())




class RouteDiscoveryDenials(unittest.TestCase):
    """Host-only route validation and no-publish-before-discovery contracts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.routes = self.root / 'src/routes/rest'
        (self.routes / 'health').mkdir(parents=True)
        (self.routes / 'health/get.ores').write_text('fixture')
        self.patch = patch.object(generator, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_route_discovery_is_deterministically_sorted(self):
        (self.routes / 'alpha').mkdir()
        (self.routes / 'alpha/post.ores').write_text('fixture')
        entries = [(method, parts) for method, parts, _ in generator.discover(self.routes)]
        self.assertEqual(entries, [('POST', ('alpha',)), ('GET', ('health',))])

    def test_unsupported_http_method_cannot_replace_existing_source(self):
        generated = self.root / 'src/generated'
        generated.mkdir()
        (generated / 'routes.ores').write_text('prior valid table')
        (self.routes / 'health/trace.ores').write_text('unsupported')
        with self.assertRaises(ValueError):
            generator.generate(3000, self.root / 'data')
        self.assertEqual((generated / 'routes.ores').read_text(), 'prior valid table')
        self.assertFalse((generated / 'config.ores').exists())

    def test_nonliteral_segment_is_rejected_before_publication(self):
        directory = self.routes / 'not.valid'
        directory.mkdir()
        (directory / 'get.ores').write_text('invalid segment')
        with self.assertRaises(ValueError):
            generator.generate(3000, self.root / 'data')
        self.assertFalse((self.root / 'src/generated/routes.ores').exists())

    def test_empty_route_tree_is_rejected_without_outputs(self):
        (self.routes / 'health/get.ores').unlink()
        with self.assertRaises(ValueError):
            generator.generate(3000, self.root / 'data')
        self.assertFalse((self.root / 'src/generated/routes.ores').exists())


class RoutePortAndStableOrder(unittest.TestCase):
    """Publisher invariants at both port bounds and across ordered routes."""
    setUp = RoutePublication.setUp

    def test_minimum_valid_port_publishes_literal_config(self):
        generator.generate(1, self.root / 'data')
        config = (self.root / 'src/generated/config.ores').read_text()
        self.assertIn('return 1;', config)
        self.assertIn('GET /health', (self.root / 'src/generated/routes.ores').read_text())

    def test_maximum_valid_port_publishes_literal_config(self):
        generator.generate(65535, self.root / 'data')
        config = (self.root / 'src/generated/config.ores').read_text()
        self.assertIn('return 65535;', config)

    def test_route_emission_order_is_stable_across_creation_order(self):
        zeta = self.root / 'src/routes/rest/zeta'
        alpha = self.root / 'src/routes/rest/alpha'
        zeta.mkdir()
        (zeta / 'post.ores').write_text('fixture')
        alpha.mkdir()
        (alpha / 'get.ores').write_text('fixture')
        generator.generate(3000, self.root / 'data')
        emitted = (self.root / 'src/generated/routes.ores').read_text()
        markers = ('GET /alpha', 'GET /health', 'POST /zeta')
        offsets = [emitted.index(marker) for marker in markers]
        self.assertEqual(offsets, sorted(offsets))



class SupportedMethodsAndPublisherAtomicity(unittest.TestCase):
    """Host-only source publication checks; no guest compiler execution."""
    setUp = RoutePublication.setUp

    def test_head_and_options_have_distinct_literal_routes(self):
        routes = self.root / 'src/routes/rest/health'
        (routes / 'head.ores').write_text('fixture')
        (routes / 'options.ores').write_text('fixture')
        generator.generate(3000, self.root / 'data')
        source = (self.root / 'src/generated/routes.ores').read_text()
        for method in ('GET', 'HEAD', 'OPTIONS'):
            with self.subTest(method=method):
                self.assertIn(f'{method} /health', source)

    def test_safe_hyphen_and_underscore_segment_is_literal(self):
        nested = self.root / 'src/routes/rest/team_alpha-3'
        nested.mkdir()
        (nested / 'post.ores').write_text('fixture')
        generator.generate(3000, self.root / 'data')
        source = (self.root / 'src/generated/routes.ores').read_text()
        self.assertIn('POST /team_alpha-3', source)
        self.assertIn('http_routing.literal("team_alpha-3")', source)

    def test_invalid_later_port_keeps_both_last_good_publications(self):
        generator.generate(3000, self.root / 'data')
        generated = self.root / 'src/generated'
        before = {name: (generated / name).read_bytes()
                  for name in ('routes.ores', 'config.ores')}
        with self.assertRaises(ValueError):
            generator.generate(65536, self.root / 'different-data')
        for name, original in before.items():
            with self.subTest(name=name):
                self.assertEqual((generated / name).read_bytes(), original)


class RouteWave14BoundaryTests(unittest.TestCase):
    """Native host generator controls; not a compiled AttoLang route execution test."""
    setUp = RoutePublication.setUp

    def test_port_endpoints_are_valid_and_rewrite_complete_config(self):
        for port in (1, 65535):
            with self.subTest(port=port):
                generator.generate(port, self.root/'data')
                config = (self.root/'src/generated/config.ores').read_text()
                self.assertIn('return '+str(port)+';', config)
                self.assertIn('GET /health', (self.root/'src/generated/routes.ores').read_text())

    def test_unicode_and_encoded_route_segments_never_publish(self):
        for name in ('bad%2Fsegment', 'control\u202e', 'two words', '.hidden'):
            with self.subTest(name=name):
                path = self.root/'src/routes/rest'/name
                path.mkdir()
                (path/'get.ores').write_text('fixture')
                with self.assertRaisesRegex(ValueError, 'literal URL segments'):
                    generator.generate(3000, self.root/'data')
                self.assertFalse((self.root/'src/generated/routes.ores').exists())
                self.assertFalse((self.root/'src/generated/config.ores').exists())
                (path/'get.ores').unlink()
                path.rmdir()

    def test_case_changed_method_cannot_replace_prior_valid_publication(self):
        generator.generate(3000, self.root/'data')
        generated = self.root/'src/generated'
        prior = {name: (generated/name).read_bytes() for name in ('routes.ores','config.ores')}
        (self.root/'src/routes/rest/health/GET.ores').write_text('bad method')
        with self.assertRaisesRegex(ValueError, 'unsupported method filename'):
            generator.generate(3001, self.root/'new-data')
        for name, data in prior.items():
            self.assertEqual((generated/name).read_bytes(),data)


class RoutePublicationBoundaries(unittest.TestCase):
    """Regression checks for idempotent publication and lexical route admission."""
    setUp = RoutePublication.setUp

    def test_repeat_publishing_with_same_inputs_is_byte_identical(self):
        generator.generate(3000, self.root / "data")
        directory = self.root / "src/generated"
        first = {name: (directory / name).read_bytes()
                 for name in ("routes.ores", "config.ores")}
        generator.generate(3000, self.root / "data")
        for name, expected in first.items():
            with self.subTest(name=name):
                self.assertEqual((directory / name).read_bytes(), expected)

    def test_multilevel_literal_segments_are_not_misinterpreted(self):
        nested = self.root / "src/routes/rest/api/v1_2"
        nested.mkdir(parents=True)
        (nested / "delete.ores").write_text("fixture")
        generator.generate(3000, self.root / "data")
        routes = (self.root / "src/generated/routes.ores").read_text()
        self.assertIn("DELETE /api/v1_2", routes)
        self.assertIn('http_routing.literal("api")', routes)
        self.assertIn('http_routing.literal("v1_2")', routes)

    def test_unicode_segment_rejection_preserves_both_previous_files(self):
        generator.generate(3000, self.root / "data")
        generated = self.root / "src/generated"
        before = {name: (generated / name).read_bytes()
                  for name in ("routes.ores", "config.ores")}
        invalid = self.root / "src/routes/rest/café"
        invalid.mkdir()
        (invalid / "get.ores").write_text("fixture")
        with self.assertRaisesRegex(ValueError, "literal URL segments"):
            generator.generate(3000, self.root / "data")
        for name, contents in before.items():
            with self.subTest(name=name):
                self.assertEqual((generated / name).read_bytes(), contents)



class Wave32EncodedConfigAndMultiMethodRoutes(unittest.TestCase):
    """Source-owned host publication regressions, not guest route execution."""
    setUp = RoutePublication.setUp

    def test_config_data_path_with_quotes_is_json_escaped(self):
        import json
        data = self.root / 'data"quoted'
        generator.generate(3000, data)
        config = (self.root / 'src/generated/config.ores').read_text()
        expected = json.dumps(str((data.resolve() / "baz.txt")))
        self.assertIn("return " + expected + ";", config)
        self.assertEqual(config.count("pub fnc data_path()"), 1)
        self.assertIn("GET /health", (self.root / 'src/generated/routes.ores').read_text())

    def test_same_literal_path_multiple_methods_have_unique_indices(self):
        directory = self.root / 'src/routes/rest/health'
        (directory / "head.ores").write_text("host fixture")
        (directory / "post.ores").write_text("host fixture")
        generator.generate(3000, self.root / 'data')
        source = (self.root / 'src/generated/routes.ores').read_text()
        for index, method in enumerate(("GET", "HEAD", "POST")):
            with self.subTest(method=method):
                self.assertIn(f"route_{index}.handler()", source)
                self.assertIn(f'{method} /health', source)
        self.assertLess(source.index("GET /health"), source.index("HEAD /health"))
        self.assertLess(source.index("HEAD /health"), source.index("POST /health"))


class RoutePortTypeWave32(unittest.TestCase):
    """Host source generation never accepts non-integral or bool port literals."""
    setUp = RoutePublication.setUp

    def test_bool_float_or_string_port_cannot_publish_generated_sources(self):
        for port in (True, False, 3000.0, 3000.5, "3000", None):
            with self.subTest(port=repr(port)):
                with self.assertRaisesRegex(ValueError, "port must be"):
                    generator.generate(port, self.root / "data")
                self.assertFalse((self.root / "src/generated/routes.ores").exists())
                self.assertFalse((self.root / "src/generated/config.ores").exists())

    def test_invalid_type_preserves_previous_successful_publications(self):
        generator.generate(3000, self.root / "data")
        generated = self.root / "src/generated"
        before = {name: (generated / name).read_bytes()
                  for name in ("routes.ores", "config.ores")}
        for port in (True, 3000.0, 3000.5):
            with self.subTest(port=repr(port)):
                with self.assertRaisesRegex(ValueError, "port must be"):
                    generator.generate(port, self.root / "other-data")
                for name, expected in before.items():
                    self.assertEqual((generated / name).read_bytes(), expected)



class RouteCaseAndEmissionWave31(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.routes = self.root / "src/routes/rest"
        (self.routes / "v1").mkdir(parents=True)
        (self.routes / "v1/get.ores").write_text("fixture", encoding="utf-8")
        active = patch.object(generator, "ROOT", self.root)
        active.start()
        self.addCleanup(active.stop)

    def test_uppercase_method_is_rejected_before_publication(self):
        (self.routes / "v1/GET.ores").write_text("fixture", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unsupported method filename"):
            generator.generate(3000, self.root / "data")
        self.assertFalse((self.root / "src/generated/routes.ores").exists())

    def test_nested_literal_segment_is_escaped_and_kept_under_root(self):
        (self.routes / "v1/team_b-2").mkdir()
        (self.routes / "v1/team_b-2/post.ores").write_text("fixture", encoding="utf-8")
        generator.generate(3000, self.root / "data")
        result = (self.root / "src/generated/routes.ores").read_text()
        self.assertIn('http_routing.path("/v1/team_b-2"', result)
        self.assertIn('http_routing.literal("team_b-2")', result)
        self.assertLess(result.index("GET /v1"), result.index("POST /v1/team_b-2"))

    def test_existing_valid_output_survives_route_discovery_failure(self):
        generated = self.root / "src/generated"
        generated.mkdir()
        original = generated / "routes.ores"
        original.write_text("prior known-good table", encoding="utf-8")
        (self.routes / "v1/Bad.Part").mkdir()
        (self.routes / "v1/Bad.Part/post.ores").write_text("invalid", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "literal URL segments"):
            generator.generate(3000, self.root / "data")
        self.assertEqual(original.read_text(), "prior known-good table")
        self.assertFalse((generated / "config.ores").exists())


class RoutePublicationWave31(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.routes = self.root / "src/routes/rest"
        self.routes.mkdir(parents=True)
        self.patch = patch.object(generator, "ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def add(self, route, method):
        path = self.routes / route / (method + ".ores")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("route fixture\n", encoding="utf-8")
        return path

    def test_nested_literal_routes_and_method_order_are_stable(self):
        self.add("users/me", "post")
        self.add("health", "get")
        generator.generate(8080, self.root / "data")
        source = (self.root / "src/generated/routes.ores").read_text()
        self.assertIn('builder.route("GET"', source)
        self.assertIn('builder.route("POST"', source)
        self.assertIn('http_routing.literal("users")', source)
        self.assertIn('http_routing.literal("me")', source)
        self.assertLess(source.index("GET /health"), source.index("POST /users/me"))

    def test_invalid_case_or_nonliteral_route_keeps_existing_artifacts(self):
        self.add("ok", "get")
        generator.generate(8080, self.root / "data")
        generated = self.root / "src/generated"
        before = {n: (generated / n).read_bytes() for n in ("routes.ores", "config.ores")}
        bad = self.add("another", "Get")
        with self.assertRaisesRegex(ValueError, "unsupported method filename"):
            generator.generate(8080, self.root / "data")
        bad.unlink()
        self.add("bad%2Fsegment", "get")
        with self.assertRaisesRegex(ValueError, "literal URL segments"):
            generator.generate(8080, self.root / "data")
        self.assertEqual(before, {n: (generated / n).read_bytes() for n in before})

    def test_generated_leaf_symlinks_do_not_overwrite_external_data(self):
        self.add("health", "get")
        generated = self.root / "src/generated"
        generated.mkdir()
        external = self.root / "outside.txt"
        external.write_text("preserve this")
        (generated / "routes.ores").symlink_to(external)
        generator.generate(8080, self.root / "data")
        self.assertEqual(external.read_text(), "preserve this")
        self.assertFalse((generated / "routes.ores").is_symlink())
        self.assertIn("GET /health", (generated / "routes.ores").read_text())

    def test_invalid_port_fails_before_any_publication(self):
        self.add("health", "get")
        for port in (0, 65536, -1):
            with self.subTest(port=port), self.assertRaisesRegex(ValueError, "port"):
                generator.generate(port, self.root / "data")
            self.assertFalse((self.root / "src/generated").exists())

class Wave36UntrustedConfigPath(unittest.TestCase):
    """Host-only source-escaping oracle; not HTTP runtime or guest execution."""
    setUp = RoutePublication.setUp

    def test_data_directory_is_json_escaped_for_control_and_quote_characters(self):
        import json
        for name in ('quote"name', 'tab\tname', 'newline\nname',
                     'backslash\\name', 'unicode-\u2028'):
            with self.subTest(name=repr(name)):
                directory = self.root / name
                generator.generate(3000, directory)
                config = (self.root / 'src/generated/config.ores').read_text()
                encoded = json.dumps(str(directory.resolve() / 'baz.txt'))
                self.assertIn('return ' + encoded + ';', config)
                self.assertEqual(config.count('pub fnc data_path()'), 1)
                self.assertIn('GET /health', (self.root / 'src/generated/routes.ores').read_text())
                self.assertNotIn('tab\tname', config)
                self.assertNotIn('newline\nname', config)
                self.assertNotIn('unicode-\u2028', config)

    def test_repeated_config_for_same_special_path_is_byte_identical(self):
        directory = self.root / 'quoted"name\\with-backslash'
        generator.generate(65535, directory)
        path = self.root / 'src/generated/config.ores'
        expected = path.read_bytes()
        generator.generate(65535, directory)
        self.assertEqual(path.read_bytes(), expected)





class Wave38PortSubclassAdmission(unittest.TestCase):
    """Host-only regression: integer-like objects cannot inject guest source."""

    setUp = RoutePublication.setUp

    def test_int_subclass_is_rejected_before_creating_generated_source(self):
        class HostilePort(int):
            def __str__(self):
                return "3000; } pub fnc injected(): int { return 9; }"

        for port in (HostilePort(3000), HostilePort(1), HostilePort(65535)):
            with self.subTest(value=int(port)):
                with self.assertRaisesRegex(ValueError, "port must be in 1..65535"):
                    generator.generate(port, self.root / "data")
                self.assertFalse((self.root / "src/generated/routes.ores").exists())
                self.assertFalse((self.root / "src/generated/config.ores").exists())

    def test_int_subclass_cannot_replace_previous_valid_publication(self):
        class HostilePort(int):
            def __str__(self):
                return "8080; injected()"

        generator.generate(8080, self.root / "data")
        generated = self.root / "src/generated"
        before = {name: (generated / name).read_bytes()
                  for name in ("routes.ores", "config.ores")}
        with self.assertRaisesRegex(ValueError, "port must be in 1..65535"):
            generator.generate(HostilePort(8080), self.root / "other-data")
        self.assertEqual(before, {name: (generated / name).read_bytes()
                                  for name in before})



class Wave40AtomicPreflightOnDiscoveredRoutes(unittest.TestCase):
    """Reject hostile route files without rewriting previously valid source."""

    setUp = RoutePublication.setUp

    def test_unsupported_method_or_encoded_segment_preserves_both_source_files(self):
        generator.generate(13000, self.root / 'known-good')
        generated = self.root / 'src/generated'
        before = {name: (generated / name).read_bytes()
                  for name in ('routes.ores', 'config.ores')}
        route_root = self.root / 'src/routes/rest'

        hostile_method = route_root / 'health/trace.ores'
        hostile_method.write_text('invalid method')
        with self.assertRaisesRegex(ValueError, 'unsupported method filename'):
            generator.generate(13001, self.root / 'new-data')
        hostile_method.unlink()

        hostile_dir = route_root / 'bad%2Fsegment'
        hostile_dir.mkdir()
        (hostile_dir / 'get.ores').write_text('invalid literal segment')
        with self.assertRaisesRegex(ValueError, 'literal URL segments'):
            generator.generate(13002, self.root / 'new-data')

        self.assertEqual(before, {name: (generated / name).read_bytes()
                                  for name in before})
        self.assertEqual(list(generated.glob('.routes-*')), [])
        self.assertFalse((self.root / 'new-data').exists())




class FailedDataPreflight(unittest.TestCase):
    # Reuse only the setup fixture, avoiding duplication of the full parent suite.
    setUp = RoutePublication.setUp

    def test_existing_generated_sources_unchanged_when_data_root_is_file(self):
        generated = self.root / 'src/generated'
        generated.mkdir(parents=True)
        routes = generated / 'routes.ores'
        config = generated / 'config.ores'
        routes.write_bytes(b'previous routes\n')
        config.write_bytes(b'previous config\n')
        invalid = self.root / 'occupied'
        invalid.write_bytes(b'not a directory')
        with self.assertRaises((FileExistsError, NotADirectoryError, OSError)):
            generator.generate(3000, invalid)
        self.assertEqual(routes.read_bytes(), b'previous routes\n')
        self.assertEqual(config.read_bytes(), b'previous config\n')

    def test_data_root_failure_does_not_create_generated_outputs(self):
        invalid_parent = self.root / 'file-parent'
        invalid_parent.write_bytes(b'occupied')
        with self.assertRaises((FileExistsError, NotADirectoryError, OSError)):
            generator.generate(3000, invalid_parent / 'child')
        self.assertFalse((self.root / 'src/generated').exists())


class RegularSourceOnly(unittest.TestCase):
    setUp = RoutePublication.setUp

    def test_directory_named_like_method_source_is_rejected_without_publication(self):
        bogus = self.root / 'src/routes/rest/health/post.ores'
        bogus.mkdir()
        with self.assertRaisesRegex(ValueError, 'regular source file'):
            generator.generate(3000, self.root / 'data')
        self.assertFalse((self.root / 'src/generated/routes.ores').exists())
        self.assertFalse((self.root / 'src/generated/config.ores').exists())

    def test_rejected_route_does_not_overwrite_previous_generated_sources(self):
        generated = self.root / 'src/generated'
        generated.mkdir(parents=True)
        (generated / 'routes.ores').write_bytes(b'old-routes')
        (generated / 'config.ores').write_bytes(b'old-config')
        (self.root / 'src/routes/rest/health/post.ores').mkdir()
        with self.assertRaisesRegex(ValueError, 'regular source file'):
            generator.generate(3000, self.root / 'data')
        self.assertEqual((generated / 'routes.ores').read_bytes(), b'old-routes')
        self.assertEqual((generated / 'config.ores').read_bytes(), b'old-config')


class Wave42PortTypeAndPublication(unittest.TestCase):
    """Non-integer port types must fail before source or data publication."""
    setUp = RoutePublication.setUp

    def test_boolean_float_text_and_int_subclasses_preserve_prior_publication(self):
        class WrappedPort(int):
            pass

        generator.generate(3000, self.root / 'data')
        generated = self.root / 'src/generated'
        originals = {name: (generated / name).read_bytes()
                     for name in ('routes.ores', 'config.ores')}
        for invalid in (True, False, 3000.0, '3000', WrappedPort(3000)):
            with self.subTest(port=repr(invalid)), self.assertRaisesRegex(ValueError, 'port must be in 1..65535'):
                generator.generate(invalid, self.root / 'blocked-data')
            for name, expected in originals.items():
                self.assertEqual((generated / name).read_bytes(), expected)
            self.assertFalse((self.root / 'blocked-data').exists())

if __name__=='__main__':
    unittest.main()
