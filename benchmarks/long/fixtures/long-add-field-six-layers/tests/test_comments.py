"""CRUD through the router for comments."""

import unittest

from tasker import db
from tasker.api.router import dispatch


class CommentTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect()

    def test_create_read_update_delete(self):
        status, created = dispatch(self.conn, "POST", "/comments", body={'ticket_id': 1, 'author_id': 1, 'body': 'Looks good'})
        self.assertEqual(status, 201)
        item_id = created["id"]
        for key, value in {'ticket_id': 1, 'author_id': 1, 'body': 'Looks good'}.items():
            self.assertEqual(created[key], value)
        self.assertEqual(dispatch(self.conn, "GET", f"/comments/{item_id}")[0], 200)
        status, updated = dispatch(self.conn, "PUT", f"/comments/{item_id}", body={'body': 'Needs work'})
        self.assertEqual(status, 200)
        for key, value in {'body': 'Needs work'}.items():
            self.assertEqual(updated[key], value)
        self.assertEqual(len(dispatch(self.conn, "GET", "/comments")[1]), 1)
        self.assertEqual(dispatch(self.conn, "DELETE", f"/comments/{item_id}")[0], 200)
        self.assertEqual(dispatch(self.conn, "GET", f"/comments/{item_id}")[0], 404)

    def test_unknown_field_is_rejected(self):
        status, payload = dispatch(self.conn, "POST", "/comments", body={**{'ticket_id': 1, 'author_id': 1, 'body': 'Looks good'}, "bogus": 1})
        self.assertEqual(status, 422)
        self.assertIn("unknown field bogus", payload["errors"])


if __name__ == "__main__":
    unittest.main()
