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

import os
import queue
import concurrent.futures

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
		self.is_cancelled = False
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
		if not self.is_cancelled:
			self.is_ready = True
		if not self.blocked():
			self.unblocked()
		return

	def blocked(self):
		return not (self.is_ready or self.is_cancelled) or len(self.depends_on) != 0

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

	def cancel(self, force=False):
		if self.is_completed:
			return

		self.is_cancelled = True
		self.is_ready = False
		if force or not self.blocked():
			self.unblocked()
		return

	def do_cancel(self):
		# Detach from all items this one depends on.
		# This only happens if this item has been cancelled with force=True
		if self.depends_on:
			list_to_detach = self.depends_on
			self.depends_on = []
			for item in list_to_detach:
				item.dependents.remove(self)

		# cancel all items which depend in this
		list_to_cancel = self.dependents
		self.dependents = []

		for item in list_to_cancel:
			item.depends_on.remove(self)
			item.cancel()

		return self.on_cancel()

	def on_cancel(self):
		return

class executor():
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
				if node.is_cancelled:
					node.do_cancel()
				elif self.is_cancelled:
					node.cancel()
				else:
					node.complete()
			if existing_only:
				break
			to_execute = self.queue

		return True

# async_executor will read items to execute from a synchronized queue
# instead of a simple list.
class async_executor(dependency_node):
	def __init__(self):
		super().__init__(executor=self)
		self.completion_queue = queue.SimpleQueue()
		return

	def add_to_completion(self, dep_node: dependency_node):
		self.completion_queue.put(dep_node)
		return

	def run(self, existing_only=False, block=False):
		if self.is_completed:
			block = False

		to_execute = self.completion_queue.qsize()
		if not block and not to_execute:
			return False

		if not existing_only:
			to_execute = -1
		elif to_execute == 0:
			to_execute = 1

		while to_execute != 0:
			try:
				node = self.completion_queue.get(block=block)
			except queue.Empty:
				break
			# An overloaded function will call completed()
			# to unblock all dependents of this node
			future = getattr(node, 'future', None)
			if future:
				if future.cancelled():
					node.is_cancelled = True
				elif self.is_cancelled:
					node.is_cancelled = True
				else:
					try:
						node.future_result = future.result()
					except BrokenPipeError:
						node.future_result = None
				node.future = None

			if node.is_cancelled:
				node.do_cancel()
			elif self.is_cancelled:
				node.cancel()
			else:
				node.complete()
			block = False
			to_execute -= 1
		return True

class async_workitem(dependency_node):
	_futures_executor = None

	def __init__(self, *dependencies, executor=None, futures_executor=None):
		super().__init__(*dependencies, executor=executor)
		if dependencies and not futures_executor:
			self.futures_executor = dependencies[0].futures_executor
		elif futures_executor:
			self.futures_executor = futures_executor
		elif self._futures_executor:
			self.futures_executor = async_workitem._futures_executor
		else:
			async_workitem._futures_executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(4, min(16, os.cpu_count())))
			self.futures_executor = async_workitem._futures_executor
		self.future = None
		self.future_result = None
		self.async_func = None
		return

	def shutdown():
		if async_workitem._futures_executor:
			async_workitem._futures_executor.shutdown(cancel_futures=True)
			async_workitem._futures_executor = None

	def async_completion_callback(self, future):
		self.executor.add_to_completion(self)
		return

	def unblocked(self):
		if self.is_cancelled:
			self.async_func = None
		if not self.async_func:
			return self.async_completion_callback(None)
		assert(self.future is None)
		self.future = self.futures_executor.submit(self.async_func, *self.async_args, **self.async_kwargs)
		self.async_func = None
		self.future.add_done_callback(self.async_completion_callback)
		return

	def set_async_func(self, func, *args, **kwargs):
		self.async_func = func
		self.async_args = args
		self.async_kwargs = kwargs
		return

	def result(self):
		future = self.future
		if future:
			self.future = None
			self.future_result = future.result()
		return self.future_result

	def __str__(self):
		return self.result()
