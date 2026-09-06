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
    # The real constraint is the total encoded size of what's actually
    # returned (header + every line + the "\n" separators between them) -
    # a budget that only counted line bytes would let the returned
    # context exceed MAX_HUNK_SCOPE_BYTES by the header/separator size, so
    # this asserts against the true serialized output, not an inflated
    # allowance for that gap.
    import scan_worker.flash_review_hunk_scope as mod

    content = "class Topic\n  class NotAllowed\n  end\n\n  has_many :x\nend\n"
    patch = "@@ -1,4 +1,6 @@ class NotAllowed\n context\n+ has_many :x\n"
    diff_patches = (("app/topic.rb", patch),)
    full_context = build_hunk_scope_correction_context({"app/topic.rb": content}, diff_patches)
    assert "lists `NotAllowed`" in full_context

    original = mod.MAX_HUNK_SCOPE_BYTES
    mod.MAX_HUNK_SCOPE_BYTES = len(full_context.encode("utf-8")) - 1
    try:
        truncated_context = build_hunk_scope_correction_context({"app/topic.rb": content}, diff_patches)
        assert len(truncated_context.encode("utf-8")) <= mod.MAX_HUNK_SCOPE_BYTES
        assert truncated_context != full_context
    finally:
        mod.MAX_HUNK_SCOPE_BYTES = original


def test_byte_budget_smaller_than_the_header_returns_empty_not_oversized():
    import scan_worker.flash_review_hunk_scope as mod
    original = mod.MAX_HUNK_SCOPE_BYTES
    mod.MAX_HUNK_SCOPE_BYTES = 10
    try:
        content = "class Topic\n  class NotAllowed\n  end\n\n  has_many :x\nend\n"
        patch = "@@ -1,4 +1,6 @@ class NotAllowed\n context\n+ has_many :x\n"
        diff_patches = (("app/topic.rb", patch),)
        context = build_hunk_scope_correction_context({"app/topic.rb": content}, diff_patches)
        assert context == ""
    finally:
        mod.MAX_HUNK_SCOPE_BYTES = original


def test_a_later_changed_line_disagreeing_is_caught_even_when_the_hunk_start_agrees():
    # Real gap found via Flash Review's own review of this module: checking
    # only the hunk's start line let a later added line genuinely inside a
    # different, nested class slip through unflagged whenever the hunk's
    # first line happened to agree with the header - exactly the failure
    # mode _hunk_claims_with_changed_lines now closes by checking every
    # added line, not just the first.
    content = """class Topic < ActiveRecord::Base
  def existing_method
    1
  end

  class NotAllowed < StandardError
    def newly_added_method
      raise "boom"
    end
  end
end
"""
    # Header claims "Topic" for the hunk's start (line 1) - correct, line 1
    # really is inside Topic. The added line lands at line 7, genuinely
    # inside the nested NotAllowed class instead.
    patch = "@@ -1,4 +1,7 @@ class Topic < ActiveRecord::Base\n a\n b\n c\n d\n e\n f\n+    def newly_added_method\n"
    diff_patches = (("app/models/topic.rb", patch),)

    context = build_hunk_scope_correction_context({"app/models/topic.rb": content}, diff_patches)

    assert "app/models/topic.rb:7" in context
    assert "lists `Topic` as" in context
    assert "actually inside `NotAllowed`" in context
