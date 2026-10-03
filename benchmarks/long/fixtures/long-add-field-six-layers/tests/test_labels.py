"""CRUD through the router for labels."""

import unittest

from tasker import db
from tasker.api.router import dispatch


class LabelTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect()

    def test_create_read_update_delete(self):
        status, created = dispatch(self.conn, "POST", "/labels", body={'name': 'bug'})
        self.assertEqual(status, 201)
        item_id = created["id"]
        for key, value in {'name': 'bug'}.items():
            self.assertEqual(created[key], value)
        self.assertEqual(dispatch(self.conn, "GET", f"/labels/{item_id}")[0], 200)
        status, updated = dispatch(self.conn, "PUT", f"/labels/{item_id}", body={'color': 'red'})
        self.assertEqual(status, 200)
        for key, value in {'color': 'red'}.items():
            self.assertEqual(updated[key], value)
        self.assertEqual(len(dispatch(self.conn, "GET", "/labels")[1]), 1)
        self.assertEqual(dispatch(self.conn, "DELETE", f"/labels/{item_id}")[0], 200)
        self.assertEqual(dispatch(self.conn, "GET", f"/labels/{item_id}")[0], 404)

    def test_unknown_field_is_rejected(self):
        status, payload = dispatch(self.conn, "POST", "/labels", body={**{'name': 'bug'}, "bogus": 1})
        self.assertEqual(status, 422)
        self.assertIn("unknown field bogus", payload["errors"])


if __name__ == "__main__":
    unittest.main()
