from aletheore.scope_lookup import enclosing_scope_for_line


def test_ruby_finds_the_innermost_real_class_not_a_sibling():
    # The exact real false positive this module exists to fix: a real
    # Discourse file (app/models/topic.rb) where a diff hunk's header named
    # a sibling exception class (`NotAllowed`) as nearby context, but the
    # actual line lives inside `Topic`'s own body, well after `NotAllowed`
    # already closed.
    content = """class Topic < ActiveRecord::Base
  class NotAllowed < StandardError
    attr_accessor :allowed_user_ids
  end

  has_many :topic_localizations, dependent: :destroy
end
"""
    assert enclosing_scope_for_line("app/models/topic.rb", content, 6) == "Topic"
    assert enclosing_scope_for_line("app/models/topic.rb", content, 3) == "NotAllowed"


def test_ruby_module_is_also_a_real_scope():
    content = """module Api
  class V1
    def show
      1
    end
  end
end
"""
    assert enclosing_scope_for_line("app/lib/api.rb", content, 3) == "V1"
    assert enclosing_scope_for_line("app/lib/api.rb", content, 7) == "Api"


def test_ruby_module_level_line_has_no_enclosing_class():
    content = "require 'foo'\nclass Bar\nend\n"
    assert enclosing_scope_for_line("lib/bar.rb", content, 1) is None


def test_ruby_malformed_source_returns_none_not_a_crash():
    assert enclosing_scope_for_line("app/broken.rb", "class Foo\n  def bar(\n", 1) is None


def test_python_finds_the_innermost_real_class():
    content = """class Outer:
    class Inner:
        def method(self):
            pass
"""
    assert enclosing_scope_for_line("app/models.py", content, 4) == "Inner"
    assert enclosing_scope_for_line("app/models.py", content, 1) == "Outer"


def test_python_module_level_line_has_no_enclosing_class():
    content = "import os\n\ndef free_function():\n    pass\n"
    assert enclosing_scope_for_line("app/utils.py", content, 3) is None


def test_python_syntax_error_returns_none_not_a_crash():
    assert enclosing_scope_for_line("app/broken.py", "class Foo(:\n", 1) is None


def test_unsupported_extension_returns_none():
    assert enclosing_scope_for_line("app.go", "package main\n\ntype Foo struct{}\n", 3) is None
