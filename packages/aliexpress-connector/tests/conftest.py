"""Marks this directory as its own pytest rootdir package.

Several connectors have a ``test_server.py``; without a conftest per directory
pytest cannot tell the identically-named modules apart during collection.
"""
