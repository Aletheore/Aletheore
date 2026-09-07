from aletheore.model_associations import rails_model_association_edges


def _files(**by_name: str) -> dict[str, bytes]:
    return {f"app/models/{name}.rb": content.encode() for name, content in by_name.items()}


def test_belongs_to_resolves_by_naming_convention():
    files = _files(
        post="class Post < ActiveRecord::Base\n  belongs_to :user\nend\n",
        user="class User < ActiveRecord::Base\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == [("app/models/post.rb", "app/models/user.rb")]


def test_has_many_resolves_pluralized_target():
    files = _files(
        post="class Post < ActiveRecord::Base\n  has_many :post_replies\nend\n",
        post_reply="class PostReply < ActiveRecord::Base\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == [("app/models/post.rb", "app/models/post_reply.rb")]


def test_explicit_class_name_wins_over_naming_convention():
    files = _files(
        post="class Post < ActiveRecord::Base\n  belongs_to :reply_to_user, class_name: \"User\"\nend\n",
        user="class User < ActiveRecord::Base\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == [("app/models/post.rb", "app/models/user.rb")]


def test_through_association_is_skipped():
    # The real Discourse case this guards: has_many :replies, through:
    # :post_replies - "replies" is not a real model/table on its own, it's
    # a virtual relation via the through model. Resolving it as if
    # "Reply" were a real class would point at nothing real, or worse, a
    # wrong file that happens to share the naming-convention guess.
    files = _files(
        post="class Post < ActiveRecord::Base\n"
        "  has_many :replies, through: :post_replies\n"
        "end\n",
        reply="class Reply < ActiveRecord::Base\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == []


def test_namespaced_model_wrapped_in_module_is_still_resolved():
    # Real, common Rails pattern this module previously missed entirely:
    # _class_name_and_superclass only checked tree.root_node.children, so
    # a class nested inside `module Admin; ...; end` was invisible - the
    # file was silently treated as not a model at all, producing zero
    # edges in either direction (as source or as target).
    files = _files(
        post="module Admin\n  class Post < ActiveRecord::Base\n    belongs_to :user\n  end\nend\n",
        user="module Admin\n  class User < ActiveRecord::Base\n  end\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == [("app/models/post.rb", "app/models/user.rb")]


def test_doubly_nested_namespaced_model_is_still_resolved():
    files = _files(
        post="module Api\n  module V1\n    class Post < ApplicationRecord\n"
        "      belongs_to :user\n    end\n  end\nend\n",
        user="class User < ApplicationRecord\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == [("app/models/post.rb", "app/models/user.rb")]


def test_auxiliary_class_before_the_real_model_does_not_hide_it():
    # A file with a plain class (no superclass) preceding the actual
    # model class - _class_nodes must keep searching past the first
    # ineligible class instead of the whole file being treated as "not a
    # model", the same way the old top-level-only loop already handled a
    # missing-superclass first class before this module supported module
    # nesting.
    files = _files(
        post="class PostValidator\nend\n\nclass Post < ActiveRecord::Base\n"
        "  belongs_to :user\nend\n",
        user="class User < ActiveRecord::Base\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == [("app/models/post.rb", "app/models/user.rb")]


def test_polymorphic_belongs_to_is_skipped():
    files = _files(
        bookmark="class Bookmark < ActiveRecord::Base\n"
        "  belongs_to :bookmarkable, polymorphic: true\n"
        "end\n",
        post="class Post < ActiveRecord::Base\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == []


def test_polymorphic_has_many_with_as_is_skipped():
    files = _files(
        post="class Post < ActiveRecord::Base\n  has_many :bookmarks, as: :bookmarkable\nend\n",
        bookmark="class Bookmark < ActiveRecord::Base\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == []


def test_belongs_to_with_explicit_polymorphic_false_still_resolves():
    # Real gap found via Flash Review's own review: polymorphic: false is
    # an explicit, valid declaration that this association is NOT
    # polymorphic - a bare presence check on the polymorphic keyword
    # wrongly skipped it the same as polymorphic: true, dropping a real,
    # resolvable, fixed-target association.
    files = _files(
        post="class Post < ActiveRecord::Base\n  belongs_to :user, polymorphic: false\nend\n",
        user="class User < ActiveRecord::Base\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == [("app/models/post.rb", "app/models/user.rb")]


def test_association_with_no_real_matching_model_produces_no_edge():
    # belongs_to :ghost has no corresponding Ghost model anywhere in this
    # scan - never guessed into an edge pointing at a file that may not
    # exist.
    files = _files(post="class Post < ActiveRecord::Base\n  belongs_to :ghost\nend\n")
    edges = rails_model_association_edges(files)
    assert edges == []


def test_non_model_file_contributes_no_edges():
    files = _files(
        helper="class SomeHelper\n  belongs_to :user\nend\n",  # not an ActiveRecord subclass
        user="class User < ActiveRecord::Base\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == []


def test_indirect_active_record_inheritance_is_walked_transitively():
    # A real Discourse pattern: Reviewable < ActiveRecord::Base, and
    # several concrete reviewable types < Reviewable. ReviewablePost is a
    # real model even though it doesn't inherit ActiveRecord::Base
    # directly, so its belongs_to :user must still produce a real edge.
    files = _files(
        reviewable="class Reviewable < ActiveRecord::Base\nend\n",
        reviewable_post="class ReviewablePost < Reviewable\n  belongs_to :user\nend\n",
        user="class User < ActiveRecord::Base\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == [("app/models/reviewable_post.rb", "app/models/user.rb")]


def test_multi_level_transitive_inheritance_is_walked():
    files = _files(
        base="class Reviewable < ActiveRecord::Base\nend\n",
        mid="class ReviewableFlaggedPost < Reviewable\nend\n",
        leaf="class ReviewableQueuedPost < ReviewableFlaggedPost\n  belongs_to :user\nend\n",
        user="class User < ActiveRecord::Base\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == [("app/models/leaf.rb", "app/models/user.rb")]


def test_a_superclass_outside_this_scan_stops_the_chain_not_a_guess():
    # SomeGemBaseClass is never defined among these files - genuinely
    # unknowable whether it ultimately reaches ActiveRecord::Base, so this
    # file must not be treated as a model.
    files = _files(
        leaf="class Foo < SomeGemBaseClass\n  belongs_to :user\nend\n",
        user="class User < ActiveRecord::Base\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == []


def test_an_inheritance_cycle_does_not_infinite_loop():
    # Malformed Ruby (A < B < A can't really exist), but this module must
    # not hang or crash on it - it just correctly finds no real
    # ActiveRecord::Base at the root and treats neither as a model.
    files = _files(
        a="class A < B\n  belongs_to :user\nend\n",
        b="class B < A\nend\n",
        user="class User < ActiveRecord::Base\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == []


def test_has_and_belongs_to_many_resolves_like_has_many():
    files = _files(
        post="class Post < ActiveRecord::Base\n  has_and_belongs_to_many :tags\nend\n",
        tag="class Tag < ActiveRecord::Base\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == [("app/models/post.rb", "app/models/tag.rb")]


def test_application_record_superclass_is_also_recognized():
    files = _files(
        post="class Post < ApplicationRecord\n  belongs_to :user\nend\n",
        user="class User < ApplicationRecord\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == [("app/models/post.rb", "app/models/user.rb")]


def test_a_model_never_produces_a_self_edge():
    files = _files(
        post="class Post < ActiveRecord::Base\n  belongs_to :post\nend\n",
    )
    edges = rails_model_association_edges(files)
    assert edges == []


def test_malformed_ruby_source_is_skipped_not_crashed():
    files = _files(broken="class Post < ActiveRecord::Base\n  belongs_to :user\n  def (((\n")
    edges = rails_model_association_edges(files)
    assert edges == []


def test_multiple_real_associations_all_resolve():
    files = _files(
        post="class Post < ActiveRecord::Base\n"
        "  belongs_to :user\n"
        "  belongs_to :topic\n"
        "  has_many :post_details\n"
        "end\n",
        user="class User < ActiveRecord::Base\nend\n",
        topic="class Topic < ActiveRecord::Base\nend\n",
        post_detail="class PostDetail < ActiveRecord::Base\nend\n",
    )
    edges = set(rails_model_association_edges(files))
    assert edges == {
        ("app/models/post.rb", "app/models/user.rb"),
        ("app/models/post.rb", "app/models/topic.rb"),
        ("app/models/post.rb", "app/models/post_detail.rb"),
    }
