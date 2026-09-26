"""Tests for the assets application.

A package rather than loose `test_*.py` files at the app root, because Django
resolves a bare app label by importing that name and looking for tests in it -
it does not walk the directory. With the modules at the root, `manage.py test
assets` reported "Found 0 test(s)" and said nothing was wrong, so the suite was
excluded from every run that named the app, including CI's fork job and every
figure quoted from it.
"""
