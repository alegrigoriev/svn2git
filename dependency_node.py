#   Copyright 2021-2023 Alexandre Grigoriev
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

## This object manages chains of dependencies.
# It's intended as a base class.
# The object uses executor to manage serialization of calls
class dependency_node:
	def __init__(self, *initial_dependencies, executor = None):
		# list of nodes depending on this object
		self.depends_on = []
		# list of nodes which depend on this node
		self.dependents = []
		# self.is_ready means the node could be executed, unless it depends on other uncompleted nodes
		self.is_ready = False
		# self.is_completed means its dependents can proceed immediately
		self.is_completed = False
		self.completion_func = None
		if initial_dependencies:
			self.executor = initial_dependencies[0].executor
			for dep in initial_dependencies:
				self.add_dependency(dep)
		else:
			self.executor = executor
		return

	## An object adds dependency when it cannot proceed without
	# the dependency having been done
	def add_dependency(self, dependency):
		assert(not self.is_completed)
		if not dependency.is_completed:
			self.depends_on.append(dependency)
			dependency.dependents.append(self)
		return

	## When an object's dependency is all done,
	# it can now be removed from the list.
	# If the list becomes empty, the object becomes unblocked
	# and can proceed with execution
	def dependency_done(self, dependency):
		self.depends_on.remove(dependency)
		if not self.blocked():
			self.unblocked()
		return

	def release_all_dependents(self):
		while self.dependents:
			dependent = self.dependents.pop(-1)
			dependent.dependency_done(self)
		return

	def set_completion_func(self, func, *args, **kwargs):
		self.completion_func = func
		self.completion_args = args
		self.completion_kwargs = kwargs
		return

	def completed(self):
		self.is_completed = True
		self.release_all_dependents()
		return

	def ready(self):
		self.is_ready = True
		if not self.blocked():
			self.unblocked()
		return

	def blocked(self):
		return not self.is_ready or len(self.depends_on) != 0

	def unblocked(self):
		self.executor.add_to_completion(self)
		return

	def complete(self):
		# Can be overloaded by the subclass
		# Is called by the executor when all dependencies are done
		# An overloaded function will eventually call completed()
		# to unblock all dependents
		if self.completion_func:
			self.completion_func(*self.completion_args, **self.completion_kwargs)
		return self.completed()

class executor:
	def __init__(self):
		self.queue = []
		return

	def add_to_completion(self, dep_node: dependency_node):
		self.queue.append(dep_node)
		return

	def run(self, existing_only=False):
		to_execute = self.queue
		if not to_execute:
			return False

		while to_execute:
			self.queue = []

			for node in to_execute:
				# An overloaded function will call completed()
				# to unblock all dependents of this node
				node.complete()
			if existing_only:
				break
			to_execute = self.queue

		return True
