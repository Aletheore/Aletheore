from scan_worker.flash_review_hunk_scope import build_hunk_scope_correction_context


def test_returns_empty_string_when_no_diff_patches():
    assert build_hunk_scope_correction_context({"a.rb": "class A\nend\n"}, None) == ""
    assert build_hunk_scope_correction_context({"a.rb": "class A\nend\n"}, ()) == ""


def test_real_discourse_mismatch_produces_a_correction_fact():
    # The exact real false positive this module exists to fix: git's hunk
    # header names `class NotAllowed < StandardError` as nearby context (its
    # own nearest-preceding-signature heuristic), but the hunk's real new-file
    # line 6 is inside `Topic`'s own body - `NotAllowed` already closed above
    # it. Verified against the real file that produced this in production
    # testing (app/models/topic.rb, Discourse PR #32440).
    content = """class Topic < ActiveRecord::Base
  class NotAllowed < StandardError
    attr_accessor :allowed_user_ids
  end

  has_many :topic_localizations, dependent: :destroy
end
"""
    patch = "@@ -1,4 +1,6 @@ class NotAllowed < StandardError\n context\n+ has_many :topic_localizations, dependent: :destroy\n"
    diff_patches = (("app/models/topic.rb", patch),)
    context = build_hunk_scope_correction_context({"app/models/topic.rb": content}, diff_patches)
    assert "app/models/topic.rb:1" in context
    assert "lists `NotAllowed` as" in context
    assert "actually inside `Topic`" in context


def test_agreeing_header_and_real_scope_produce_no_fact():
    content = "class Foo\n  def bar\n    1\n  end\nend\n"
    patch = "@@ -1,2 +1,3 @@ class Foo\n context\n+  # comment\n"
    diff_patches = (("app/foo.rb", patch),)
    context = build_hunk_scope_correction_context({"app/foo.rb": content}, diff_patches)
    assert context == ""


def test_header_naming_a_def_not_a_class_produces_no_fact():
    # This module only has a real scope_lookup to check a class/module claim
    # against - a def/method header claim isn't something it can verify, so
    # it must stay silent rather than guess.
    content = "class Foo\n  def bar\n    1\n  end\nend\n"
    patch = "@@ -1,2 +1,3 @@ def bar\n context\n+  # comment\n"
    diff_patches = (("app/foo.rb", patch),)
    context = build_hunk_scope_correction_context({"app/foo.rb": content}, diff_patches)
    assert context == ""


def test_missing_file_content_is_skipped_not_crashed():
    patch = "@@ -1,2 +1,3 @@ class Foo\n context\n+  # comment\n"
    diff_patches = (("app/foo.rb", patch),)
    context = build_hunk_scope_correction_context({}, diff_patches)
    assert context == ""


def test_unsupported_language_produces_no_fact():
    content = "package main\n\ntype Foo struct{}\n"
    patch = "@@ -1,2 +1,3 @@ class Foo\n context\n+// comment\n"
    diff_patches = (("main.go", patch),)
    context = build_hunk_scope_correction_context({"main.go": content}, diff_patches)
    assert context == ""


def test_byte_budget_truncates_rather_than_failing():
    import scan_worker.flash_review_hunk_scope as mod
    original = mod.MAX_HUNK_SCOPE_BYTES
    mod.MAX_HUNK_SCOPE_BYTES = 10
    try:
        content = "class Topic\n  class NotAllowed\n  end\n\n  has_many :x\nend\n"
        patch = "@@ -1,4 +1,6 @@ class NotAllowed\n context\n+ has_many :x\n"
        diff_patches = (("app/topic.rb", patch),)
        context = build_hunk_scope_correction_context({"app/topic.rb": content}, diff_patches)
        assert context == "" or len(context.encode("utf-8")) <= 10 + len(
            "--- hunk-header scope corrections (verified against real file content) ---\n"
        )
    finally:
        mod.MAX_HUNK_SCOPE_BYTES = original
