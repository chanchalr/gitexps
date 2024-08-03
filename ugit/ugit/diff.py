import subprocess
from collections import defaultdict
from tempfile import NamedTemporaryFile as Temp
from . import data


def compare_trees(*trees):
    entries = defaultdict(lambda: [None] * len(trees))
    for i, tree in enumerate(trees):
        for path, oid in tree.items():
            entries[path][i] = oid
    for path,oids in entries.items():
        yield (path, *oids)


def diff_trees(t_from,t_to):
    output = ''
    for path,o_from,o_to in compare_trees(t_from,t_to):
        if o_from != o_to:
            output += "\n" + diff_blobs(o_from, o_to,path)
    return output


def diff_blobs(o_from,o_to, path='blob'):
    with Temp() as f_from, Temp() as f_to:
        for oid, f in ((o_from, f_from),(o_to, f_to)):
            if oid:
                f.write(data.get_object(oid))
                f.flush()
        with subprocess.Popen(
            ['diff','--unified','--show-c-function','--label',f'a/{path}',f_from.name,'--label',f'b/{path}',f_to.name],
            stdout=subprocess.PIPE) as proc:
            output, _ = proc.communicate()
        return output.decode()


def iter_changed_files(t_from, t_to):
    for path, o_from, o_to in compare_trees(t_from, t_to):
        if o_from != o_to:
            action = ('new file' if o_from is None else
                      'deleted' if o_to is None else
                      'modified')
            yield path, action


def merge_trees(t_base, t_HEAD, t_other):
    tree = {}
    for path,o_base, o_HEAD, o_other in compare_trees(t_base, t_HEAD, t_other):
        tree[path] = data.hash_object(merge_blob(o_base, o_HEAD, o_other))
    return tree


def merge_blob(o_base, o_HEAD, o_other):
    with Temp() as f_base, Temp() as f_HEAD, Temp() as f_other:
        for oid, f in ((o_base, f_base), (o_HEAD, f_HEAD), (o_other, f_other)):
            if oid:
                f.write(data.get_object(oid))
                f.flush()
        with subprocess.Popen(['diff3', '-m',
                               '-L', 'HEAD', f_HEAD.name,
                               '-L', 'BASE', f_base.name,
                               '-L', 'MERGE_HEAD', f_other.name],
                            stdout=subprocess.PIPE) as proc:
            output, _ = proc.communicate()
            return output
