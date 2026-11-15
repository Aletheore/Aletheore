from aletheore.endpoints import map_api_endpoints


def _routes(tmp_path, code, name="app.rb"):
    (tmp_path / name).write_text(code)
    return sorted((e["method"], e["path"], e["unresolved"]) for e in map_api_endpoints(tmp_path)["endpoints"] if e["framework"] == "sinatra")


def test_verbs_with_do_blocks_and_brace_blocks(tmp_path):
    code = """require 'sinatra'
get '/users/:id' do
  'x'
end
post("/items") { 'y' }
delete '/items/:id' do
end
"""
    assert _routes(tmp_path, code) == [
        ("DELETE", "/items/:id", False), ("GET", "/users/:id", False), ("POST", "/items", False),
    ]


def test_namespace_blocks_compose_prefixes_in_a_modular_app(tmp_path):
    code = """require 'sinatra/base'
class App < Sinatra::Base
  namespace '/api' do
    get '/ping' do
    end
    namespace '/v2' do
      post '/items' do
      end
    end
  end
end
"""
    assert _routes(tmp_path, code) == [("GET", "/api/ping", False), ("POST", "/api/v2/items", False)]


def test_route_with_several_verbs(tmp_path):
    code = "require 'sinatra'\nroute :get, :post, '/multi' do\nend\n"
    assert _routes(tmp_path, code) == [("GET", "/multi", False), ("POST", "/multi", False)]


def test_interpolated_path_is_unresolved(tmp_path):
    code = 'require "sinatra"\nget "/#{PREFIX}/x" do\nend\n'
    assert _routes(tmp_path, code) == [("GET", "/", True)]


def test_ruby_file_that_does_not_mention_sinatra_is_ignored(tmp_path):
    assert _routes(tmp_path, "get '/x' do\nend\n") == []
