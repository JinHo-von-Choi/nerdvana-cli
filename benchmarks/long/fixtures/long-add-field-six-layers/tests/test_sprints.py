"""CRUD through the router for sprints."""

import unittest

from tasker import db
from tasker.api.router import dispatch


class SprintTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect()

    def test_create_read_update_delete(self):
        status, created = dispatch(self.conn, "POST", "/sprints", body={'project_id': 1, 'name': 'Sprint 1'})
        self.assertEqual(status, 201)
        item_id = created["id"]
        for key, value in {'project_id': 1, 'name': 'Sprint 1'}.items():
            self.assertEqual(created[key], value)
        self.assertEqual(dispatch(self.conn, "GET", f"/sprints/{item_id}")[0], 200)
        status, updated = dispatch(self.conn, "PUT", f"/sprints/{item_id}", body={'goal': 'Ship it'})
        self.assertEqual(status, 200)
        for key, value in {'goal': 'Ship it'}.items():
            self.assertEqual(updated[key], value)
        self.assertEqual(len(dispatch(self.conn, "GET", "/sprints")[1]), 1)
        self.assertEqual(dispatch(self.conn, "DELETE", f"/sprints/{item_id}")[0], 200)
        self.assertEqual(dispatch(self.conn, "GET", f"/sprints/{item_id}")[0], 404)

    def test_unknown_field_is_rejected(self):
        status, payload = dispatch(self.conn, "POST", "/sprints", body={**{'project_id': 1, 'name': 'Sprint 1'}, "bogus": 1})
        self.assertEqual(status, 422)
        self.assertIn("unknown field bogus", payload["errors"])


if __name__ == "__main__":
    unittest.main()
