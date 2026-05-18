"""
RFC 6962 Merkle Tree primitives for AURA — Sprint 19 (Pillar 3
deepening: TRAIGA Federation).

Anchors:
  * Laurie, B., Langley, A. & Kasper, E. (2013). "Certificate
    Transparency." RFC 6962. https://datatracker.ietf.org/doc/html/rfc6962
    (Section 2.1 — the leaf / internal-node hash prefixing this
    module implements verbatim.)
  * Cobbe, J., Veale, M. & Singh, J. (FAccT '23). "Understanding
    Accountability in Algorithmic Supply Chains."

Why the prefix bytes (0x00 for leaf, 0x01 for internal node) matter
--------------------------------------------------------------------
Without prefixes a Merkle tree is vulnerable to a **second-preimage
attack**: an attacker who knows two intermediate node hashes ``h_a``
and ``h_b`` whose concatenation ``H(h_a || h_b)`` equals some
existing leaf hash can substitute the entire subtree for that leaf
without invalidating any proof that didn't touch the substituted
subtree. RFC 6962's fix is to hash leaves as ``H(0x00 || leaf_data)``
and internal nodes as ``H(0x01 || left_hash || right_hash)``. The
prefix byte makes leaf and internal hashes draw from disjoint
preimage spaces; a leaf hash can never equal an internal hash and
the subtree-substitution attack is ruled out.

What this module ships
----------------------

* ``leaf_hash(leaf_data: bytes) -> bytes``
    SHA-256 of ``0x00 || leaf_data``.

* ``node_hash(left: bytes, right: bytes) -> bytes``
    SHA-256 of ``0x01 || left || right``.

* ``build_tree_root(leaves: list[bytes]) -> bytes``
    Root hash of the Merkle tree over ``leaves`` (already-hashed
    leaf bytes — caller is responsible for calling ``leaf_hash``).
    Returns ``H(b"")`` for an empty list (matches RFC 6962 § 2.1
    convention so the empty-tree STH is well-defined).

* ``inclusion_proof(leaves: list[bytes], index: int) -> list[bytes]``
    The proof path for leaf at ``index``. Each element is the
    sibling hash at one level of the tree, ordered from leaf to root.

* ``verify_inclusion(leaf: bytes, index: int, tree_size: int,
                     proof: list[bytes], root: bytes) -> bool``
    Reconstructs the root from ``leaf + proof`` and compares to
    ``root``. Returns True iff they match. Pure-function — usable
    client-side with zero network access; the auditor only needs
    the leaf, the proof, the tree size, and the published root.

The implementation is the unbalanced Merkle tree variant from RFC
6962 § 2.1: when a level has an odd number of nodes, the right-most
node is promoted unchanged to the next level (NOT duplicated as in
some other Merkle conventions). Bit-fiddling routines below
implement RFC 6962's MTH and PATH algorithms directly so an
external auditor running an independent Python re-implementation
can verify our roots without porting algorithm differences.
"""
from __future__ import annotations

import hashlib
from typing import List, Sequence

# RFC 6962 § 2.1 leaf and internal-node hash prefixes
_LEAF_PREFIX = b"\x00"
_NODE_PREFIX = b"\x01"


def leaf_hash(leaf_data: bytes) -> bytes:
    """SHA-256 of (0x00 || leaf_data). RFC 6962 § 2.1.

    ``leaf_data`` is the raw record bytes — for the AURA audit log,
    typically the line's UTF-8 encoding without the trailing newline.
    The 0x00 prefix puts leaf hashes in a disjoint preimage space
    from internal-node hashes (prefix 0x01), preventing the second-
    preimage subtree-substitution attack.
    """
    return hashlib.sha256(_LEAF_PREFIX + leaf_data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    """SHA-256 of (0x01 || left || right). RFC 6962 § 2.1.

    Both ``left`` and ``right`` MUST be 32-byte SHA-256 digests
    (either from ``leaf_hash`` or from a previous ``node_hash``
    call). The 0x01 prefix disjoints the internal-node preimage
    space from leaf hashes.
    """
    if len(left) != 32 or len(right) != 32:
        raise ValueError(
            f"node_hash arguments must be 32 bytes each (SHA-256 digests); "
            f"got len(left)={len(left)}, len(right)={len(right)}"
        )
    return hashlib.sha256(_NODE_PREFIX + left + right).digest()


def build_tree_root(leaves: Sequence[bytes]) -> bytes:
    """Compute the Merkle Tree Hash (MTH) of a sequence of leaf hashes.

    RFC 6962 § 2.1 MTH algorithm, recursive form:

        MTH({})  = SHA-256("")              # empty tree convention
        MTH({d}) = d                        # single leaf is the root
        MTH(D)   = node_hash(MTH(D[:k]), MTH(D[k:]))
                   where k is the largest power of 2 strictly less than |D|

    The largest-power-of-2 split produces an unbalanced tree where
    every left subtree is a complete binary tree and the right
    subtree fills the remainder — RFC 6962 § 2.1's specific shape,
    which all CT clients can reconstruct from any size.

    Args:
        leaves: list of 32-byte SHA-256 leaf hashes (already
            prefixed via ``leaf_hash``).

    Returns:
        32-byte root hash.
    """
    n = len(leaves)
    if n == 0:
        # RFC 6962 § 2.1: MTH({}) = SHA-256("")
        return hashlib.sha256(b"").digest()
    if n == 1:
        return leaves[0]
    k = _largest_power_of_two_less_than(n)
    left = build_tree_root(leaves[:k])
    right = build_tree_root(leaves[k:])
    return node_hash(left, right)


def inclusion_proof(leaves: Sequence[bytes], index: int) -> List[bytes]:
    """Build the inclusion proof for leaf at ``index`` in the tree.

    RFC 6962 § 2.1.1 PATH algorithm:

        PATH(m, D[n])
          if n == 1: return []
          k = largest power of 2 strictly less than n
          if m < k:
              return PATH(m, D[:k]) + [MTH(D[k:])]
          else:
              return PATH(m - k, D[k:]) + [MTH(D[:k])]

    The returned list orders proof elements from leaf-level to
    root-level — the same order ``verify_inclusion`` consumes them.

    Args:
        leaves: list of leaf hashes (already prefixed via ``leaf_hash``).
        index: position of the target leaf in ``leaves``.

    Returns:
        List of sibling hashes along the path from leaf to root.
        Empty list when the tree contains a single leaf (no
        siblings to prove against).

    Raises:
        IndexError if ``index`` is out of range.
    """
    n = len(leaves)
    if not 0 <= index < n:
        raise IndexError(f"index {index} out of range for tree of size {n}")
    if n == 1:
        return []
    k = _largest_power_of_two_less_than(n)
    if index < k:
        return inclusion_proof(leaves[:k], index) + [build_tree_root(leaves[k:])]
    return inclusion_proof(leaves[k:], index - k) + [build_tree_root(leaves[:k])]


def verify_inclusion(
    leaf: bytes,
    index: int,
    tree_size: int,
    proof: Sequence[bytes],
    root: bytes,
) -> bool:
    """Verify that ``leaf`` is at position ``index`` in a tree of
    size ``tree_size`` with root ``root``, given the proof path.

    Pure-function reconstruction following RFC 6962 § 2.1.1:
    walk up the tree, at each level deciding whether the current
    hash is the LEFT or RIGHT child based on the index's bit
    pattern, combine with the next proof element, repeat until the
    proof is consumed.

    Returns False on any mismatch (proof length wrong for tree
    size, hash mismatch at any level, computed root != expected
    root). Never raises — auditors get a clean boolean.
    """
    if not 0 <= index < tree_size:
        return False
    if tree_size == 1:
        # Single-leaf tree — the leaf is the root and the proof is empty.
        return len(proof) == 0 and leaf == root
    if len(leaf) != 32 or len(root) != 32:
        return False

    computed = leaf
    last_node = tree_size - 1
    fn = index
    ln = last_node
    proof_iter = iter(proof)
    try:
        while ln > 0:
            if fn % 2 == 1 or fn == ln:
                # We are a right child OR the right-most node at this level
                # (which RFC 6962 promotes unchanged when the level size is odd).
                if fn % 2 == 1:
                    sibling = next(proof_iter)
                    computed = node_hash(sibling, computed)
                # If fn == ln and fn is even, no sibling at this level —
                # the right-most node carries straight up.
            else:
                sibling = next(proof_iter)
                computed = node_hash(computed, sibling)
            fn //= 2
            ln //= 2
        # Any unconsumed proof elements mean the proof was longer than
        # the tree shape allows — that's a mismatch.
        if any(True for _ in proof_iter):
            return False
    except StopIteration:
        # Proof shorter than the tree shape requires
        return False
    return computed == root


# ── internals ─────────────────────────────────────────────────────────


def _largest_power_of_two_less_than(n: int) -> int:
    """Largest k = 2^p such that k < n. Caller guarantees n >= 2.

    Used as the split point in RFC 6962's MTH/PATH algorithms. For
    n=2 returns 1; n=3 returns 2; n=5 returns 4; n=8 returns 4; etc.
    """
    if n < 2:
        raise ValueError(f"_largest_power_of_two_less_than requires n >= 2; got {n}")
    # The high bit of (n-1) is the largest power of 2 <= n-1, which
    # is the largest power of 2 strictly less than n.
    return 1 << ((n - 1).bit_length() - 1)


__all__ = [
    "leaf_hash",
    "node_hash",
    "build_tree_root",
    "inclusion_proof",
    "verify_inclusion",
]
