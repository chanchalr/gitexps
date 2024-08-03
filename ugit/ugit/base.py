import os
import operator
import itertools
import string
from collections import namedtuple, deque
from . import data
from . import diff

Commit = namedtuple('Commit', ['tree', 'parents', 'message'])


def init():
    data.init()
    data.update_ref('HEAD', data.Refvalue(symbolic=True, value='refs/heads/master'))


def write_tree(directory='.'):
    index_as_tree = {}
    with data.get_index() as index:
        for path, oid in index.items():
            path = path.split("/")
            dirpath , filename = path[:-1], path[-1]
            current = index_as_tree
            for dirname in dirpath:
                current = current.setdefault(dirname, {})
            current[filename] = oid

    def write_tree_recursive(tree_dict):
        entries = []
        for name, value in tree_dict.items():
            if type(value) is dict:
                type_ = 'tree'
                oid = write_tree_recursive(value)
            else:
                type_ = 'blob'
                oid = value
            entries.append((name, oid, type_))
        tree = ''.join(f'{type_} {oid} {name}\n' for name, oid, type_ in sorted(entries))
        return data.hash_object(tree.encode(), 'tree')

    return write_tree_recursive(index_as_tree)

def _empty_current_directory():
    for root, dirnames, filenames in os.walk('.',topdown=False):
        for filename in filenames:
            path = os.path.relpath(f'{root}/{filename}')
            if is_ignored(path):
                continue
            os.remove(path)
        for dirname in dirnames:
            path = os.path.relpath(f'{root}/{dirname}')
            if is_ignored(path):
                continue
            try:
                os.rmdir(path)
            except (FileNotFoundError,OSError):
                pass   # Directories may not be empty, ignore this error


def _iter_tree_entries(oid):
    if not oid:
        return
    tree = data.get_object(oid, "tree")
    for entry in tree.decode().split('\n'):
        if not entry:
            continue
        type_, oid, name = entry.split(' ')
        yield type_, oid, name


def get_tree(oid, base_path=''):
    result = {}
    special_dirs = {".", ".."}
    for type_, oid, name in _iter_tree_entries(oid):
        if '/' in name:
            print(f'Warning: Skipping file "{name}" as it is not supported')
            continue
        if name in special_dirs:
            continue
        path = base_path + name
        if type_ == 'blob':
            result[path] = oid
        elif type_ == 'tree':
            result.update(get_tree(oid, f'{path}/'))
        else:
            raise Exception(f'Unknown tree entry {type_}')
    return result


def read_tree(tree_oid, update_working=False):
    with data.get_index() as index:
        index.clear()
        index.update(get_tree(tree_oid))
        if update_working:
            _checkout_index(index)


def read_tree_merged(t_base, t_HEAD, t_other,update_working=False):
    with data.get_index() as index:
        index.clear()
        index.update(diff.merge_trees(get_tree(t_base),get_tree(t_HEAD),get_tree(t_other)))
        if update_working:
            _checkout_index(index)


def _checkout_index(index):
    _empty_current_directory()
    for path, oid in index.items():
        os.makedirs(os.path.dirname(f'./{path}'), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data.get_object(oid))


def is_ignored(path):
    return '.ugit' in path.split('/')


def commit(message):
    commit_data = f'tree {write_tree()}\n'
    HEAD = data.get_ref('HEAD').value
    if HEAD:
        commit_data += f'parent {HEAD}\n'
    MERGE_HEAD = data.get_ref('MERGE_HEAD').value
    if MERGE_HEAD:
        commit_data += f'parent {MERGE_HEAD}\n'
        data.delete_ref('MERGE_HEAD', deref=False)
    commit_data += '\n'
    commit_data += f'{message}\n'
    oid = data.hash_object(commit_data.encode(), 'commit')
    data.update_ref('HEAD', data.Refvalue(symbolic=False, value=oid))
    return oid


def checkout(name):
    oid = get_oid(name)
    oid_commit = get_commit(oid)
    read_tree(oid_commit.tree,update_working=True)
    if is_branch(name):
        HEAD = data.Refvalue(symbolic=True, value=f'refs/heads/{name}')
    else:
        HEAD = data.Refvalue(symbolic=False, value=name)
    data.update_ref('HEAD', HEAD, deref=False)


def is_branch(branch):
    return data.get_ref(f'refs/heads/{branch}').value


def get_commit(oid):
    parents = []
    tree = None
    commit = data.get_object(oid, 'commit').decode()
    lines = iter(commit.splitlines())
    for line in itertools.takewhile(operator.truth, lines):
        key, value = line.split(' ')
        if key == 'tree':
            tree = value
        elif key == 'parent':
            parents.append(value)
        else:
            assert False, f'Unknown field {key}'
    message = '\n'.join(lines)
    return Commit(tree=tree, parents=parents, message=message)


def get_oid(name):
    if name == '@':
        name = 'HEAD'
    refs_to_try = [
        f'{name}',
        f'refs/{name}',
        f'refs/tags/{name}',
        f'refs/heads/{name}',
    ]
    for ref in refs_to_try:
        if data.get_ref(ref, deref=False).value:
            return data.get_ref(ref).value
    is_hex = all(c in string.hexdigits for c in name)
    if len(name) == 40 and is_hex:
        return name
    assert False, f'Unknown name {name}'


def create_tag(name,oid):
    data.update_ref(f'refs/tags/{name}', data.Refvalue(symbolic=False, value=oid))


def iter_commits_and_parents(oids):
    oids = deque(oids)
    visited = set()
    while oids:
        oid = oids.popleft()
        if not oid or oid in visited:
            continue
        visited.add(oid)
        yield oid
        commit = get_commit(oid)
        oids.extendleft(commit.parents[:1])
        oids.extend(commit.parents[1:])


def is_ancestor_of(commit, maybe_ancestor):
    return maybe_ancestor in iter_commits_and_parents({commit})


def iter_objects_in_commits(oids):
    visited = set()

    def iter_objects_in_tree(oid):
        visited.add(oid)
        yield oid
        for type_, oid, _ in _iter_tree_entries(oid):
            if oid not in visited:
                if type_ == 'tree':
                    yield from iter_objects_in_tree(oid)
                else:
                    visited.add(oid)
                    yield oid
    for oid in iter_commits_and_parents(oids):
        yield oid
        commit = get_commit(oid)
        if commit.tree not in visited:
            yield from iter_objects_in_tree(commit.tree)



def create_branch(name, oid):
    data.update_ref(f'refs/heads/{name}', data.Refvalue(symbolic=False,value=oid))


def get_branch_name():
    HEAD = data.get_ref('HEAD', deref=False)
    if not HEAD.symbolic:
        return None
    HEAD = HEAD.value
    assert HEAD.startswith('refs/heads/')
    return os.path.relpath(HEAD, 'refs/heads/')


def iter_branch_names():
    for refname, _ in data.iter_refs('refs/heads/'):
        yield os.path.relpath(refname, 'refs/heads/')


def reset(oid):
    data.update_ref('HEAD', data.Refvalue(symbolic=False, value=oid))


def get_working_tree():
    result = {}
    for root, _, filenames in os.walk('.'):
        for filename in filenames:
            path = os.path.relpath(f'{root}/{filename}')
            if is_ignored(path):
                continue
            with open(path,'rb') as f:
                result[path] = data.hash_object(f.read())
    return result


def merge(other):
    HEAD = data.get_ref('HEAD').value
    assert HEAD
    merge_base = get_merge_base(HEAD, other)
    c_other = get_commit(other)
    if merge_base == HEAD:
        read_tree(c_other.tree,update_working=True)
        data.update_ref('HEAD', data.Refvalue(symbolic=False, value=other))
        print("Fast forwarded merge no need to commit")
        return
    c_base = get_commit(merge_base)
    c_HEAD = get_commit(HEAD)
    data.update_ref('MERGE_HEAD', data.Refvalue(symbolic=False, value=other))
    read_tree_merged(c_base.tree, c_HEAD.tree, c_other.tree,update_working=True)
    print("Merged in working directory\nPlease commit")


def get_merge_base(oid1, oid2):
    parents1 = set()
    for parent in iter_commits_and_parents({oid1}):
        parents1.add(parent)
    for oid in iter_commits_and_parents({oid2}):
        if oid in parents1:
            return oid


def get_index_tree():
    with data.get_index() as index:
        return index


def add(filenames):
    def add_file(filename):
        filename = os.path.relpath(filename)
        with open(filename, 'rb') as f:
            index[filename] = data.hash_object(f.read())

    def add_directory(dirname):
        for root, _, filenames in os.walk(dirname):
            for filename in filenames:
                path = os.path.relpath(f'{root}/{filename}')
                if is_ignored(path) or not os.path.isfile(path):
                    continue
                add_file(path)

    with data.get_index() as index:
        for filename in filenames:
            if os.path.isfile(filename):
                add_file(filename)
            elif os.path.isdir(filename):
                add_directory(filename)