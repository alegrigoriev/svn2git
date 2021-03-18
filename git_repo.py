import os
import sys
import subprocess
from pathlib import Path
import concurrent.futures

### GIT: controls the aspects of conversion of SVN revision to Git repo(s)
class GIT:
	TOTAL_GIT_HASHED_FILES = 0
	TOTAL_GIT_HASHED_SIZE = 0
	TOTAL_GIT_COMMITS_MADE = 0

	def __init__(self, path=None):
		self.repo_path = Path(path)
		# List of queued ref updates. "git update-ref --stdin" is used to run bulk update
		self.pending_ref_updates = []
		self.futures_executor = concurrent.futures.ThreadPoolExecutor(max_workers=min(32, os.cpu_count()+4))

		return

	def shutdown(self):
		return self.futures_executor.shutdown()

	def get_cwd(self, env={}):
		if not env:
			return self.repo_path
		return env.get('GIT_WORK_TREE', self.repo_path)

	### hash_object function invokes Git hash-object command to hash the data blob and write it
	# to the repository object database. The data is hashed as-is, without any conversion
	def hash_object(self, data, path=None, env=None):
		if not self.repo_path:
			return None
		p = subprocess.Popen(["git", "-c", "core.safecrlf=false", "hash-object", "-t", "blob", "-w", "--stdin",
					("--path=" + path) if path else "--no-filters"],
					stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=self.get_cwd(env), env=env)
		if not p:
			return None
		p.stdin.write(data)
		p.stdin.close()
		sha1 = p.stdout.readline().decode().rstrip('\n')

		GIT.TOTAL_GIT_HASHED_FILES += 1
		GIT.TOTAL_GIT_HASHED_SIZE += len(data)
		return sha1

	### hash_object_async function invokes Git to hash the data blob and write it
	# to the repository object database.
	# The result is returned asynchronously, through a proxy object
	# async_sha1.
	def hash_object_async(self, data, path=None, env=None):

		class async_sha1:
			def __init__(self, git, data, path, env):
				self.future = git.futures_executor.submit(git.hash_object, data, path, env)
				return

			def __str__(self):
				if self.future:
					self.sha1 = self.future.result()
					self.future = None

				return self.sha1

		return async_sha1(self, data, path, env)

	def make_env(self, work_dir, index_file):
		return {'GIT_WORK_TREE' : work_dir, 'GIT_INDEX_FILE' : index_file}

	def update_index(self, env=None):
		return subprocess.Popen(["git", "update-index", "--add", "--force-remove", "--index-info"],
						stdin=subprocess.PIPE, cwd=self.get_cwd(env),
						env=env)

	def read_tree(self, *options, env=None):
		p = subprocess.Popen(["git", "read-tree", *options],
						stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, cwd=self.get_cwd(env),
						env=env)
		if not p:
			return None
		p.wait()
		if p.returncode:
			raise subprocess.CalledProcessError(p.returncode, "git read-tree")

		return

	def write_tree(self, env=None):
		p = subprocess.Popen(["git", "write-tree"],
						stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, cwd=self.repo_path,
						env=env)
		if not p:
			return None
		sha1 = p.stdout.readline().decode().rstrip('\n')
		p.wait()
		p.stdout.close()
		if p.returncode:
			raise subprocess.CalledProcessError(p.returncode, "git write-tree")

		return sha1

	def config(self, env=None):
		p = subprocess.Popen(["git", "config", "--list"],
						stdin=subprocess.DEVNULL, stdout=sys.stdout, cwd=self.repo_path,
						env=env)
		if p:
			p.wait()

		return

	def get_git_dir(self, absolute=True):
		p = subprocess.Popen(["git", "-C", self.repo_path, "rev-parse", "--absolute-git-dir" if absolute else "--git-dir"],
						stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
		if not p:
			return None
		result = p.stdout.readline().decode().rstrip('\n')
		p.wait()
		p.stdout.close()
		if p.returncode:
			raise subprocess.CalledProcessError(p.returncode, "git rev-parse")
		return result

	def log(self, *options):
		p = subprocess.Popen(["git", "log", *options],
						stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
						cwd=self.repo_path)
		if not p:
			return None

		result = bytes()
		while True:
			out = p.stdout.read()
			if not out:
				break
			result += out

		p.wait()
		p.stdout.close()

		if p.returncode:
			raise subprocess.CalledProcessError(p.returncode, "git log")
		return result.decode()

	def show(self, *options):
		p = subprocess.Popen(["git", "show", *options],
						stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
						cwd=self.repo_path)
		if not p:
			return None

		result = bytes()
		while True:
			out = p.stdout.read()
			if not out:
				break
			result += out

		p.wait()
		p.stdout.close()

		if p.returncode:
			raise subprocess.CalledProcessError(p.returncode, "git show")
		return result.decode()

	def for_each_ref(self, *options):
		empty = []
		p = subprocess.Popen(["git", "for-each-ref", *options],
						stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
						cwd=self.repo_path)
		if not p:
			return empty

		while True:
			out = p.stdout.readline()
			if not out:
				break
			yield out.rstrip(b'\n').decode()
			# If we haven't read a single line, we would return an empty list
			# Now that we returned a line, change the return to None, to signal end of generator
			empty = None

		p.wait()
		p.stdout.close()

		if p.returncode:
			raise subprocess.CalledProcessError(p.returncode, "git for-each-ref")
		return empty

	def tag(self, tagname, sha1, message : list, tagger, email, date, *options, env=None):
		if not env:
			env = {}
		else:
			env = env.copy()
		if tagger:
			env["GIT_COMMITTER_NAME"] = tagger
			env["GIT_COMMITTER_EMAIL"] = email

		if date:
			env["GIT_COMMITTER_DATE"] = date

		arg_list = ["git", "tag", tagname, sha1, '-a', *options]

		for msg in message:
			arg_list += ['-m', msg]

		p = subprocess.Popen(arg_list,
						stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
						cwd=self.repo_path, env=env)
		if not p:
			return None

		p.wait()

		if p.returncode:
			raise subprocess.CalledProcessError(p.returncode, "git tag")
		return

	def tag_info(self, refname):
		class taginfo:
			pass

		ii = self.for_each_ref(refname, '--format=%(objecttype)\n%(*objecttype)\n%(*objectname)\n%(taggername)\n%(taggeremail:trim)\n%(taggerdate:iso-strict)\n%(contents)\n')

		if next(ii, '') != 'tag\n':
			return None
		info = taginfo()

		info.type = next(ii, '').strip()
		info.sha1 = next(ii, '').strip()
		info.author = next(ii, '').strip()
		info.email = next(ii, '').strip()
		info.date = next(ii, '').strip()
		info.log = ''.join(*ii).strip()

		return info

	def commit_tree(self, tree, parents, message_list,
				author_name=None, author_email=None, author_date=None,
				committer_name=None, committer_email=None, committer_date=None,
				env=None):
		# the commit ID will be output on stdout
		if not env:
			env = {}
		else:
			env = env.copy()

		if author_name:
			env["GIT_AUTHOR_NAME"] = author_name
			if not author_email:
				author_email = author_name + '@localhost'
			env["GIT_AUTHOR_EMAIL"] = author_email

		if author_date:
			env["GIT_AUTHOR_DATE"] = author_date

		if committer_name:
			env["GIT_COMMITTER_NAME"] = committer_name
			if not committer_email:
				committer_email = committer_name + '@localhost'
			env["GIT_COMMITTER_EMAIL"] = committer_email

		if committer_date:
			env["GIT_COMMITTER_DATE"] = committer_date

		options_list = []
		for parent in parents:
			options_list += ['-p', parent]

		if not message_list:
			message_list = ['No message']

		p = subprocess.Popen(["git", "commit-tree", tree, *options_list],
						stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=self.repo_path,
						env=env)
		if not p:
			return None

		p.stdin.write('\n\n'.join(message_list).encode(encoding='utf=8'))
		p.stdin.close()

		commit = p.stdout.readline().decode().rstrip('\n')
		p.wait()
		p.stdout.close()
		if p.returncode:
			raise subprocess.CalledProcessError(p.returncode, "git commit-tree")

		GIT.TOTAL_GIT_COMMITS_MADE += 1
		return commit

	def queue_update_ref(self, ref, sha1):
		return self.pending_ref_updates.append((ref, sha1))

	def commit_refs_update(self):
		if not self.pending_ref_updates:
			return
		p = subprocess.Popen(["git", "update-ref", "--stdin"],
					stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, cwd=self.repo_path)
		try:
			for ref, sha1 in self.pending_ref_updates:
				p.stdin.write(bytes('update "%s" %s\n' % (ref, sha1), encoding='utf-8'))
		except OSError:
			#print("OSError thrown for ref %s sha %s", file=sys.stderr)
			exit(22)
		p.stdin.close()
		p.wait()

		self.pending_ref_updates = []

def print_stats(fd):
	print("Git hash-object invoked: %d times, %d MiB hashed" % (
		GIT.TOTAL_GIT_HASHED_FILES, GIT.TOTAL_GIT_HASHED_SIZE//0x100000), file=fd)
	print("Git commits made: %d" % (GIT.TOTAL_GIT_COMMITS_MADE), file=fd)
