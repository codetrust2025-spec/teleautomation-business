"""One definition of "who is this candidate" for every part of Teleautomation.

The system grew four separate answers to that question:

* ``candidate_identity_links`` — derived once per backend start by migration 010
  from phone, personal email, mailbox email and explicit relationships.
* ``candidate_store.candidate_identity_ids`` — the application resolver, which
  also collapses rows by canonical *name* through ``_CANDIDATE_NAME_ALIASES``.
* ``_collapse_profile_candidates`` — the Candidates page display winner, which
  is what a mailbox is attached to at OAuth-connect time.
* ``bgv_register.profile_key`` — ``name|phone``, in a JSON file.

Reconciliation used the first one alone, through a single ``COALESCE`` hop.
That is unsafe for three independent reasons, all provable from the migration
SQL rather than from any particular row:

1. **Buckets, not components.** Migration 010 groups by phone, then by personal
   email, then by mailbox email, assigning ``min(id)`` inside each group
   separately.  A person whose rows are joined by phone *and* by mailbox can
   land in two different buckets, because the groups are never unioned.
2. **Same-priority updates are skipped.**  Every rule ends with
   ``WHERE EXCLUDED.match_priority < candidate_identity_links.match_priority``.
   Re-deriving ``VERIFIED_PHONE`` over an existing ``VERIFIED_PHONE`` row is
   ``3 < 3`` → false, so a row keeps a stale canonical forever once a
   lexicographically smaller id joins its group.
3. **No transitivity guarantee.**  Nothing makes
   ``canonical(canonical(x)) == canonical(x)``, so one hop can stop halfway
   along a chain.

Strong and weak evidence
------------------------

Identity is computed as a connected component, but not every relationship is
equal, and transitivity makes a weak edge dangerous in a way it never was for
a single lookup.

**Strong edges** join unconditionally: an explicit profile relationship, an
existing link row, a shared phone identity, a shared personal email, a shared
mailbox address. Each of these is an identifier the person actually holds.

**Name is a weak edge.** Two different people genuinely share a name, and
under transitivity one shared name would fuse both of their clusters — so a
name group that spans *conflicting* strong identities (more than one distinct
phone, or more than one distinct personal email, across the components it
would join) produces no edges at all. Ambiguity resolves to "separate",
because refusing to link is recoverable and wrongly linking one person's
interview evidence to another's booking is not.

That guard is what keeps "Reddy Charan M S" (carrying the phone) joined to
"Ram Charan M S" (carrying none) while keeping two different people named
"Rahul Sharma" apart.

Deliberate non-goal: this module does not decide which record should survive a
merge.  ``cluster_representative`` returns a stable *label*, never an identity
ruling, and nothing here merges, rewrites or deletes candidate data.  Deciding
a surviving record is a data decision that needs human evidence.
"""

from __future__ import annotations

from typing import Any, Iterable

from features import candidate_store


# One cluster can legitimately span a person's profile row plus every interview
# clone. It cannot legitimately span the whole table: that would mean the
# closure has joined unrelated people through a shared blank key, so refuse
# rather than silently treat everyone as one candidate.
MAX_CLUSTER_SIZE = 64

# The only link method that records a decision rather than a recomputation.
# Migration 010 writes it from payload canonical_candidate_id/profile_candidate_id,
# so it carries a human assertion; VERIFIED_PHONE, VERIFIED_PERSONAL_EMAIL,
# GMAIL_ACCOUNT_MAPPING and SELF are all derived from keys the resolver reads
# directly, and are the mappings known to go stale.
STRONG_LINK_METHODS = frozenset({"EXPLICIT_PROFILE_RELATIONSHIP"})


class IdentityClusterTooLarge(RuntimeError):
    """The closure joined implausibly many rows — refuse rather than guess."""


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _phone_key(value: Any) -> str:
    return candidate_store.candidate_phone_identity(value)


def _email_key(value: Any) -> str:
    key = _clean(value).casefold()
    return key if "@" in key else ""


def _name_key(value: Any) -> str:
    """Canonical-name key, so "Reddy Charan M S" and "Ram Charan M S" agree."""
    canonical = candidate_store.canonical_candidate_name(_clean(value))
    return candidate_store._normalise_candidate_name_key(canonical)


def _is_profile_row(row: dict[str, Any]) -> bool:
    return (
        candidate_store._normalise_service_type(row.get("service_type"), row)
        != "round_wise"
    )


def _explicit_parent(row: dict[str, Any]) -> str:
    return _clean(row.get("canonical_candidate_id")) or _clean(
        row.get("profile_candidate_id")
    )


class _Components:
    """Union-find whose root is always the lowest id, so results are stable."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, node: str) -> str:
        self.parent.setdefault(node, node)
        root = node
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[node] != root:  # path compression
            self.parent[node], node = root, self.parent[node]
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        low, high = sorted((left_root, right_root))
        self.parent[high] = low

    def members(self, node: str) -> set[str]:
        root = self.find(node)
        return {key for key in self.parent if self.find(key) == root}


def _fetch_links(cur) -> list[tuple[str, str, str, bool]]:
    cur.execute(
        """SELECT alias_candidate_id,canonical_candidate_id,match_method,verified
           FROM candidate_identity_links"""
    )
    return [
        (
            str(row[0] or ""),
            str(row[1] or ""),
            str(row[2] or "").strip().upper(),
            bool(row[3]),
        )
        for row in cur.fetchall()
    ]


def _fetch_mailboxes(cur) -> list[tuple[str, str]]:
    cur.execute(
        """SELECT candidate_id,lower(email_address)
           FROM candidate_mailboxes WHERE email_address LIKE '%@%'"""
    )
    return [(str(row[0] or ""), str(row[1] or "")) for row in cur.fetchall()]


def _fetch_candidate_rows(cur) -> list[dict[str, Any]]:
    """Identity columns for every candidate row.

    Name matching runs through ``_CANDIDATE_NAME_ALIASES``, which SQL cannot
    express, so the rows come back to Python. This is the same working set
    ``candidate_store._load()`` already materializes on every list call.
    """
    cur.execute(
        """SELECT id,
                  payload->>'name',
                  payload->>'phone',
                  payload->>'email',
                  payload->>'service_type',
                  payload->>'canonical_candidate_id',
                  payload->>'profile_candidate_id'
           FROM candidates_store"""
    )
    return [
        {
            "id": str(row[0] or ""),
            "name": row[1],
            "phone": row[2],
            "email": row[3],
            "service_type": row[4],
            "canonical_candidate_id": row[5],
            "profile_candidate_id": row[6],
        }
        for row in cur.fetchall()
    ]


def _strong_components(
    rows: dict[str, dict[str, Any]],
    links: Iterable[tuple[str, str, str, bool]],
    mailboxes: Iterable[tuple[str, str]],
) -> _Components:
    """Join every row that shares an identifier the person actually holds.

    Link rows are admitted here only when they record a human-declared profile
    relationship. Every other method in that table is *derived* — migration 010
    recomputed it from keys — and derived history must not outrank the raw
    evidence it was derived from. Those become guarded weak edges instead.
    """
    components = _Components()
    for cid in rows:
        components.find(cid)

    for alias, canonical, method, verified in links:
        if alias and canonical and verified and method in STRONG_LINK_METHODS:
            components.union(alias, canonical)

    for cid, row in rows.items():
        parent = _explicit_parent(row)
        if parent:
            components.union(cid, parent)

    for group in (_phone_key, _email_key):
        buckets: dict[str, list[str]] = {}
        field = "phone" if group is _phone_key else "email"
        for cid, row in rows.items():
            key = group(row.get(field))
            if key:
                buckets.setdefault(key, []).append(cid)
        for members in buckets.values():
            for other in members[1:]:
                components.union(members[0], other)

    by_address: dict[str, list[str]] = {}
    for cid, address in mailboxes:
        if cid and address:
            by_address.setdefault(address, []).append(cid)
    for members in by_address.values():
        for other in members[1:]:
            components.union(members[0], other)

    return components


def _conflicting(members: Iterable[str], rows: dict[str, dict[str, Any]]) -> bool:
    """Would this set hold two different phones or two different emails?"""
    phones, emails = set(), set()
    for cid in members:
        row = rows.get(cid)
        if not row:
            continue
        phone = _phone_key(row.get("phone"))
        if phone:
            phones.add(phone)
        email = _email_key(row.get("email"))
        if email:
            emails.add(email)
    return len(phones) > 1 or len(emails) > 1


def _weak_groups(
    rows: dict[str, dict[str, Any]], links: Iterable[tuple[str, str, str, bool]]
) -> list[tuple[str, frozenset[str]]]:
    """Candidate joins that are suggestive but not proof of identity.

    Two kinds, both conflict-guarded:

    * canonical name — round-wise support rows repeat a name without being
      that person's profile, so they never join by name, mirroring
      ``candidate_store.candidate_identity_ids``;
    * derived ``candidate_identity_links`` rows — the phone/email/mailbox/self
      groupings migration 010 recomputes, which are exactly the mappings we
      know go stale and inconsistent.
    """
    groups: list[tuple[str, frozenset[str]]] = []

    by_name: dict[str, set[str]] = {}
    for cid, row in rows.items():
        if not _is_profile_row(row):
            continue
        key = _name_key(row.get("name"))
        if key:
            by_name.setdefault(key, set()).add(cid)
    groups.extend(
        (f"name:{key}", frozenset(ids)) for key, ids in by_name.items() if len(ids) > 1
    )

    by_canonical: dict[str, set[str]] = {}
    for alias, canonical, method, verified in links:
        if not (alias and canonical) or alias == canonical:
            continue
        if verified and method in STRONG_LINK_METHODS:
            continue  # already applied as a strong edge
        by_canonical.setdefault(canonical, set()).update({alias, canonical})
    groups.extend(
        (f"link:{canonical}", frozenset(ids))
        for canonical, ids in by_canonical.items()
        if len(ids) > 1
    )

    return sorted(groups)


def _apply_weak_edges(
    components: _Components,
    rows: dict[str, dict[str, Any]],
    groups: list[tuple[str, frozenset[str]]],
) -> None:
    """Apply weak joins one whole region at a time, or not at all.

    Evaluating weak edges one pair at a time is not safe even in a fixed
    order. Given ``A(phone 111) — B(no identifiers) — C(phone 222)`` joined by
    two weak edges, accepting A–B first makes B–C conflicting, and accepting
    B–C first makes A–B conflicting. Both orders are reproducible, but which
    side keeps B is then decided by iteration order rather than by evidence.

    So weak edges are evaluated by *region*: the connected sub-graph they form
    over strong components. If anything anywhere in a region contradicts —
    two phones, two personal emails — the entire region's weak edges are
    discarded and every strong component in it keeps its own identity. B joins
    neither side, because nothing in the data says which side it belongs to.

    This is deliberately conservative: one contradiction can discard weak
    evidence elsewhere in the same region. That is the fail-closed direction,
    and a refused link surfaces as a recoverable CANDIDATE_MISMATCH.
    """
    regions = _Components()
    for _label, ids in groups:
        roots = sorted({components.find(cid) for cid in ids})
        for other in roots[1:]:
            regions.union(roots[0], other)

    by_region: dict[str, set[str]] = {}
    for root in list(regions.parent):
        by_region.setdefault(regions.find(root), set()).add(root)

    for _region_root, strong_roots in sorted(by_region.items()):
        if len(strong_roots) < 2:
            continue
        reachable: set[str] = set()
        for root in strong_roots:
            reachable |= components.members(root)
        if _conflicting(reachable, rows):
            continue
        ordered = sorted(strong_roots)
        for root in ordered[1:]:
            components.union(ordered[0], root)


def identity_cluster(cur, candidate_id: str) -> frozenset[str]:
    """Every candidate id that provably belongs to the same person.

    Strong identifiers join unconditionally; canonical name joins only when it
    cannot fuse conflicting identities. The result is a connected component, so
    it is the same whichever member it starts from — which is what makes it
    transitive and idempotent, unlike a single ``candidate_identity_links`` hop.
    """
    seed = _clean(candidate_id)
    if not seed:
        return frozenset()

    rows = {row["id"]: row for row in _fetch_candidate_rows(cur) if row["id"]}
    links = _fetch_links(cur)
    components = _strong_components(rows, links, _fetch_mailboxes(cur))
    _apply_weak_edges(components, rows, _weak_groups(rows, links))

    cluster = components.members(seed) | {seed}
    if len(cluster) > MAX_CLUSTER_SIZE:
        raise IdentityClusterTooLarge(
            f"Identity closure for {seed} exceeded {MAX_CLUSTER_SIZE} rows; "
            "a blank or shared identity key has joined unrelated candidates."
        )
    return frozenset(cluster)


def same_identity(cur, left: str, right: str) -> bool:
    """True when both ids provably describe one person."""
    if _clean(left) == _clean(right):
        return bool(_clean(left))
    left_cluster = identity_cluster(cur, left)
    if not left_cluster:
        return False
    return _clean(right) in left_cluster


def cluster_representative(cur, cluster: Iterable[str]) -> str:
    """A stable label for one cluster — *not* a ruling on which record wins.

    An explicit relationship recorded by a human wins, because that is the one
    signal in the data that someone actually asserted. Otherwise the lowest id
    is used purely so the label does not move between calls; it carries no
    evidential weight and must never be read as "this is the surviving record".
    """
    members = sorted({_clean(value) for value in cluster if _clean(value)})
    if not members:
        return ""
    rows = {row["id"]: row for row in _fetch_candidate_rows(cur)}
    declared = {
        _explicit_parent(rows[cid]) for cid in members if cid in rows
    }
    declared = {value for value in declared if value and value in members}
    if len(declared) == 1:
        return next(iter(declared))
    return members[0]
