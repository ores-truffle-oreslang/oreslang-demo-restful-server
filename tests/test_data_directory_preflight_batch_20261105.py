"""Host-only atomicity regression for a non-directory data destination."""
import unittest
from test_route_publication import RoutePublication, generator

class FailedDataPreflight(RoutePublication):
    # Inherit only the setUp fixture; do not rerun parent tests in this suite.
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

if __name__ == '__main__':
    unittest.main()
