import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from eda_agent.tools import ipc_batch


class FakeBridge:
    def __init__(self, output, *, create=True, step=True, error=False, text=False):
        self.output, self.create, self.step, self.error = output, create, step, error
        self.text, self.calls = text, []
        self.dispatch_params = ''

    async def send_command_async(self, command, args, **kwargs):
        self.calls.append(command)
        if command == 'generic.run_process':
            self.dispatch_params = args['params']
            if self.create:
                (self.output / 'test.PcbLib').write_bytes(b'fake')
                (self.output / 'report.html').write_text('<tr><td>input.csv</td><td>0</td><td>%s</td><td>1</td></tr>' % int(self.error))
            return {'dispatched': True}
        if command == 'library.get_footprints':
            return {'footprints': [{'name': 'QFN20'}]}
        if command == 'library.get_footprint_primitives':
            return {'body_models': [{'has_model': True, 'model_type': 1}] if self.step else [],
                    'texts': ([{'text': '.Designator', 'layer': 'Mechanical13'},
                               {'text': 'KEEP', 'layer': 'Mechanical13'}] if self.text else [])}
        if command == 'library.get_pad_geometry':
            return {'pad_count': 21}
        if command == 'application.save_document':
            return {'saved': True}
        raise AssertionError(f'Unexpected mutation {command}')


class BatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'input.csv'
        self.source.write_text('QFN,,\nFootprintName,DensityLevel,Other\nQFN20,N,\n')
        self.output = self.root / 'result'

    async def run_fake(self, fake):
        with patch.object(ipc_batch, 'get_bridge', return_value=fake), patch.object(
                ipc_batch.pcblib_models, 'extract_embedded_models', return_value={
                    'ok': True, 'written': [{'step_header': True, 'checksum_verified': True}]}):
            return await ipc_batch.generate_ipc_batch(str(self.source), str(self.output))

    async def test_checks_outputs_and_real_step(self):
        fake = FakeBridge(self.output)
        result = await self.run_fake(fake)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['footprint_count'], 1)
        self.assertFalse(result['ui_automation'])
        self.assertIn('|EnableStepModel=True', fake.dispatch_params)
        self.assertTrue((self.output / 'mcp-ipc-batch-result.json').is_file())

    async def test_dispatch_alone_is_not_success(self):
        fake = FakeBridge(self.output, create=False)
        result = await self.run_fake(fake)
        self.assertFalse(result['ok'])
        self.assertEqual(result['stage'], 'output')

    async def test_native_error_refuses_readback_and_save(self):
        fake = FakeBridge(self.output, error=True)
        result = await self.run_fake(fake)
        self.assertEqual(result['stage'], 'native_report')
        self.assertNotIn('application.save_document', fake.calls)

    async def test_extruded_or_missing_model_is_not_step(self):
        result = await self.run_fake(FakeBridge(self.output, step=False))
        self.assertEqual(result['stage'], '3d')
        self.assertFalse(result['ok'])

    async def test_preserves_unrelated_text(self):
        fake = FakeBridge(self.output, text=True)
        result = await self.run_fake(fake)
        self.assertEqual(result['stage'], 'designator')
        self.assertNotIn('library.delete_footprint_primitives', fake.calls)

    async def test_existing_directory_never_overwritten(self):
        self.output.mkdir()
        target = self.output / 'keep.PcbLib'
        target.write_bytes(b'keep')
        result = await self.run_fake(FakeBridge(self.output))
        self.assertEqual(result['stage'], 'preflight')
        self.assertEqual(target.read_bytes(), b'keep')

    async def test_rejects_pipe_injection(self):
        result = await ipc_batch.generate_ipc_batch(str(self.source)+'|FileName=x', str(self.output))
        self.assertEqual(result['stage'], 'preflight')

    async def test_csv_bad_density(self):
        self.source.write_text('QFN,,\nFootprintName,DensityLevel,Other\nQFN20,,\n')
        result = await self.run_fake(FakeBridge(self.output))
        self.assertEqual(result['stage'], 'preflight')


class ModelExportTests(unittest.TestCase):
    def test_duplicate_names_keep_distinct_records(self):
        import io, zlib
        from eda_agent.fileio import pcblib_models as models
        blobs = [b'ISO-10303-21;FIRST', b'ISO-10303-21;SECOND']
        class Ole:
            def openstream(self, name):
                return io.BytesIO(zlib.compress(blobs[int(name.rsplit('/', 1)[1])]))
            def close(self): pass
        def summary(i, rec, ole):
            return {'name': 'same.step', 'has_data': True,
                    'checksum': models.model_checksum(blobs[i]), 'index': i}
        with tempfile.TemporaryDirectory() as temp, patch.object(models, '_open', return_value=Ole()), patch.object(models, '_records', return_value=[{}, {}]), patch.object(models, '_summary', side_effect=summary):
            result = models.extract_embedded_models('fake.PcbLib', temp, per_record_directory=True)
            self.assertTrue(result['ok'], result)
            self.assertEqual([Path(m['path']).read_bytes() for m in result['written']], blobs)
            self.assertNotEqual(result['written'][0]['path'], result['written'][1]['path'])


if __name__ == '__main__':
    unittest.main()
