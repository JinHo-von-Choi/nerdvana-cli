"""CRUD through the router for users."""

import unittest

from tasker import db
from tasker.api.router import dispatch


class UserTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect()

    def test_create_read_update_delete(self):
        status, created = dispatch(self.conn, "POST", "/users", body={'name': 'Ada', 'email': 'ada@example.com'})
        self.assertEqual(status, 201)
        item_id = created["id"]
        for key, value in {'name': 'Ada', 'email': 'ada@example.com'}.items():
            self.assertEqual(created[key], value)
        self.assertEqual(dispatch(self.conn, "GET", f"/users/{item_id}")[0], 200)
        status, updated = dispatch(self.conn, "PUT", f"/users/{item_id}", body={'role': 'admin'})
        self.assertEqual(status, 200)
        for key, value in {'role': 'admin'}.items():
            self.assertEqual(updated[key], value)
        self.assertEqual(len(dispatch(self.conn, "GET", "/users")[1]), 1)
        self.assertEqual(dispatch(self.conn, "DELETE", f"/users/{item_id}")[0], 200)
        self.assertEqual(dispatch(self.conn, "GET", f"/users/{item_id}")[0], 404)

    def test_unknown_field_is_rejected(self):
        status, payload = dispatch(self.conn, "POST", "/users", body={**{'name': 'Ada', 'email': 'ada@example.com'}, "bogus": 1})
        self.assertEqual(status, 422)
        self.assertIn("unknown field bogus", payload["errors"])


if __name__ == "__main__":
    unittest.main()
