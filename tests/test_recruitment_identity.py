import pytest

from services import recruitment_identity as identity


def test_persisted_identity_follows_links_without_display_row_selection(monkeypatch):
    monkeypatch.setattr(identity, 'load_links', lambda: {'mailbox': 'old-slot', 'old-slot': 'person'})
    assert identity.canonical_candidate_id('mailbox') == 'person'
    assert identity.aliases('mailbox', identity.load_links()) == {'mailbox', 'old-slot', 'person'}


def test_missing_link_keeps_original_identity():
    assert identity.resolve('source', {}) == 'source'


def test_corrupt_cycle_fails_closed():
    with pytest.raises(ValueError, match='Cyclic'):
        identity.resolve('a', {'a': 'b', 'b': 'a'})
