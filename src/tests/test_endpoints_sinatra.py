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


def test_grape_resources_route_params_and_class_prefix(tmp_path):
    code = """class API < Grape::API
  prefix :api
  version 'v1', using: :path
  resource :users do
    get do
    end
    route_param :id do
      get do
      end
      delete do
      end
    end
    namespace :admin do
      post 'ban' do
      end
    end
  end
end
"""
    got = sorted((e["method"], e["path"]) for e in map_api_endpoints_in(tmp_path, code) if e["framework"] == "grape")
    assert got == [
        ("DELETE", "/api/v1/users/:id"), ("GET", "/api/v1/users"), ("GET", "/api/v1/users/:id"), ("POST", "/api/v1/users/admin/ban"),
    ]


def map_api_endpoints_in(tmp_path, code):
    (tmp_path / "api.rb").write_text(code)
    return map_api_endpoints(tmp_path)["endpoints"]


def test_ruby_file_with_non_utf8_bytes_does_not_abort_the_scan(tmp_path):
    (tmp_path / "app.rb").write_bytes(b"require 'sinatra'\n# caf\xe9\nget '/x' do\nend\n")
    assert [e["path"] for e in map_api_endpoints(tmp_path)["endpoints"] if e["framework"] == "sinatra"] == ["/x"]
