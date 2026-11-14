import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from app import Application,make_server

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=Application(Path(self.temp.name),Path(self.temp.name)/'data')
        self.url=make_server(self.app)

    def tearDown(self):
        self.app.server.shutdown();self.app.server.server_close();self.temp.cleanup()

    def test_capability_url_required(self):
        origin=self.url.rsplit('/',2)[0]
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(origin+'/api/state')
        self.assertEqual(error.exception.code,404)

    def test_foreign_host_rejected(self):
        request=urllib.request.Request(self.url+'api/ping',headers={'Host':'attacker.example'})
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
        self.assertEqual(error.exception.code,403)

    def test_api_has_no_cors_and_no_state_changes(self):
        with urllib.request.urlopen(self.url+'api/ping') as response:
            self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))
            self.assertEqual(json.load(response)['app'],'Luma')
        request=urllib.request.Request(self.url+'api/state',method='POST',data=b'{}')
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
        self.assertEqual(error.exception.code,501)

    def test_static_path_traversal_is_not_served(self):
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(self.url+'../launch-config.json')
        self.assertEqual(error.exception.code,404)

if __name__=='__main__':unittest.main()
