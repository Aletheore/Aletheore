from __future__ import annotations
import ast
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from .source import GitSource

EXCLUDED = {'.git', '.venv', 'venv', 'node_modules', 'dist', 'build', '__pycache__'}

@dataclass(frozen=True)
class Symbol:
    key: str
    path: str
    name: str
    start: int
    end: int
    kind: str

@dataclass(frozen=True)
class Edge:
    kind: str
    target: str
    caller: str
    path: str
    line: int
    end: int
    consumer: str = ''

class Index:
    def __init__(self, source: GitSource, includes: tuple[str, ...] = ()):
        self.source = source
        self.includes = includes
        self.symbols: dict[str, Symbol] = {}
        self.by_line: dict[tuple[str,int],str] = {}
        self.edges: list[Edge] = []
        self.trees: dict[str, ast.Module] = {}
        self.imports: dict[str, dict[str, tuple[str, str]]] = {}
        self.scoped_imports: dict[str, dict[str, tuple[str,str]]] = defaultdict(dict)
        self.local_names: dict[str, set[str]] = defaultdict(set)
        self.parents: dict[str, dict[ast.AST, ast.AST]] = {}
        self.aliases: dict[str, set[str]] = defaultdict(set)
        self.unsupported: dict[str, str] = {}
        self.incoming: dict[str, list[Edge]] = defaultdict(list)
        self.outgoing: dict[str, list[Edge]] = defaultdict(list)
        self.docstrings: dict[str, str] = {}
        for path in source.blobs:
            parts = PurePosixPath(path).parts
            if any(p in EXCLUDED for p in parts): continue
            if includes and not any(path == root or path.startswith(root.rstrip('/')+'/') for root in includes): continue
            if not path.endswith('.py'):
                self.unsupported[path] = 'language adapter unavailable (Python v1)'
                continue
            if len(source.read(path).encode()) > 2_000_000:
                self.unsupported[path] = 'source exceeds 2 MB indexing limit'
                continue
            try:
                tree = ast.parse(source.read(path), filename=path)
            except (SyntaxError, ValueError, RecursionError):
                self.unsupported[path] = 'Python syntax could not be indexed'
                continue
            self.trees[path] = tree
            self.docstrings[path] = ast.get_docstring(tree) or ''
            module = path[:-3].replace('/', '.')
            if module.endswith('.__init__'): module = module[:-9]
            bits = module.split('.')
            for i in range(len(bits)):
                self.aliases['.'.join(bits[i:])].add(path)
            parents = {child: n for n in ast.walk(tree) for child in ast.iter_child_nodes(n)}
            self.parents[path] = parents
            self._symbols(path, tree, '')
        for path, tree in self.trees.items():
            for node in ast.walk(tree):
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                    key=self.by_line[path,node.lineno]
                    args=node.args
                    self.local_names[key].update(a.arg for a in [*args.posonlyargs,*args.args,*args.kwonlyargs])
                    self.local_names[key].update(a.arg for a in [args.vararg,args.kwarg] if a)
                elif isinstance(node,ast.Name) and isinstance(node.ctx,ast.Store):
                    self.local_names[self.scope(path,node)].add(node.id)
            self.imports[path] = self._imports(path, tree)
        for path, tree in self.trees.items(): self._edges(path, tree)
        # Stable ordering makes budget selection and regeneration reproducible.
        self.edges = sorted(set(self.edges), key=lambda e: (e.path,e.line,e.kind,e.target,e.consumer,e.end,e.caller))
        for edge in self.edges:
            self.incoming[edge.target].append(edge)
            self.outgoing[edge.caller].append(edge)

    def _symbols(self, path, node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                key = path + '::' + name
                self.symbols[key] = Symbol(key,path,name,child.lineno,child.end_lineno,type(child).__name__)
                self.by_line[path,child.lineno] = key
                self._symbols(path,child,name+'.')
            else: self._symbols(path,child,prefix)

    def module(self, dotted: str) -> str | None:
        matches = self.aliases.get(dotted,set())
        return next(iter(matches)) if len(matches)==1 else None

    def _imports(self, path, tree):
        for node in ast.walk(tree):
            result = self.scoped_imports[self.scope(path,node)]
            if isinstance(node,ast.ImportFrom):
                module = node.module or ''
                if node.level:
                    package = path.rsplit('/',1)[0].split('/')
                    keep = len(package)-node.level+1
                    if keep < 0: continue
                    module = '.'.join(package[:keep] + ([module] if module else []))
                target = self.module(module)
                if target:
                    for alias in node.names:
                        if alias.name != '*': result[alias.asname or alias.name] = (target,alias.name)
            elif isinstance(node,ast.Import):
                for alias in node.names:
                    target = self.module(alias.name)
                    if target and alias.asname: result[alias.asname] = (target,'')
                    elif target: result[alias.name] = (target,'')
        return self.scoped_imports[path+'::<module>']

    def scope(self, path, node):
        parent = node
        while parent in self.parents[path]:
            parent = self.parents[path][parent]
            if isinstance(parent,(ast.FunctionDef,ast.AsyncFunctionDef)):
                key=self.by_line.get((path,parent.lineno))
                if key:return key
        return path+'::<module>'

    def resolve(self, path, expr, caller='') -> str | None:
        try: dotted = ast.unparse(expr)
        except (ValueError,RecursionError):return None
        if not isinstance(expr,(ast.Name,ast.Attribute)):
            return None
        first=dotted.split('.',1)[0]
        prefix=caller.split('::',1)[-1]
        scopes=[]
        while prefix and prefix!='<module>':
            scopes.append(path+'::'+prefix)
            prefix=prefix.rsplit('.',1)[0] if '.' in prefix else ''
        scopes.append(path+'::<module>')
        for scope in scopes:
            # A parameter or assignment shadows global imports and definitions.
            # Rebinding/control flow is not inferred; omit uncertain targets.
            if first in self.local_names[scope]:
                return None
            bindings=self.scoped_imports[scope]
            for name in sorted(bindings,key=len,reverse=True):
                if dotted==name or dotted.startswith(name+'.'):
                    target,attr=bindings[name]
                    suffix=dotted[len(name):].lstrip('.')
                    return target+'::'+'.'.join(p for p in [attr,suffix] if p)
            key=(path+'::'+dotted if scope.endswith('::<module>') else scope+'.'+dotted)
            if key in self.symbols:
                return key
        return None

    def _edges(self, path, tree):
        for node in ast.walk(tree):
            caller=self.scope(path,node)
            if isinstance(node,ast.Call):
                target=self.resolve(path,node.func,caller)
                if target:self.edges.append(Edge('call',target,caller,path,node.lineno,node.end_lineno))
                if target:
                    for arg in [*node.args,*(kw.value for kw in node.keywords)]:
                        callback=self.resolve(path,arg,caller) if isinstance(arg,(ast.Name,ast.Attribute)) else None
                        if callback in self.symbols:
                            self.edges.append(Edge('callback_argument',callback,caller,path,node.lineno,node.end_lineno,target))
            elif isinstance(node,ast.Constant) and isinstance(node.value,str) and '.' in node.value:
                # Explicit dotted string target, e.g. monkeypatch.setattr or mock.patch.
                # Restrict to string arguments of patch/setattr/getattr calls.
                parent=self.parents[path].get(node)
                if not isinstance(parent,ast.Call):continue
                name=ast.unparse(parent.func).rsplit('.',1)[-1]
                if name not in ('setattr','getattr','patch','delattr'):continue
                parts=node.value.split('.')
                for i in range(len(parts)-1,0,-1):
                    module=self.module('.'.join(parts[:i]))
                    if module:
                        target=module+'::'+'.'.join(parts[i:])
                        self.edges.append(Edge('dynamic_attribute',target,caller,path,node.lineno,node.end_lineno))
                        break

    def serialized(self):
        return {'schema_version':1,'revision':self.source.revision,'includes':self.includes,
                'symbols':[asdict(v) for v in self.symbols.values()],
                'edges':[asdict(e) for e in self.edges],
                'coverage':{'indexed_python_files':len(self.trees),'unindexed':self.unsupported},
                'limitations':['Static Python references only; unresolved or ambiguous imports are not guessed.',
                              'Aliases, object receiver types, reflection, star imports and runtime dispatch are incomplete.',
                              'Observed dependencies are evidence, not claims that a defect exists.']}
