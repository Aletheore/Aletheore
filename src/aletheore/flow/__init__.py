"""Flow refinement: a small abstract interpreter over tree-sitter syntax trees.

It answers one question about a scanner finding: can the value that reaches the sink be proven
not attacker controlled? It drops a finding only when every value reaching the sink is a
constant or otherwise provably benign. Anything it cannot model degrades to "unknown", which keeps
the finding, so refinement can lower false positives but never silently lower recall.
"""
