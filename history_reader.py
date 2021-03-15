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

import sys
import time
import datetime

class history_reader:
	def __init__(self, options):
		self.revisions = []
		self.last_rev = None
		self.options = options
		self.quiet = getattr(options, 'quiet', False)
		self.progress = getattr(options, 'progress', 1.)

		return

	def print_progress_message(self, msg, end=None):
		if not self.quiet:
			print(msg, end=end, file=sys.stderr)
		return

	def update_progress(self, rev):
		if self.progress is not None and time.monotonic() - self.last_progress_time >= self.progress:
			self.print_progress_line(rev)
			self.last_progress_time = time.monotonic()
		return

	def print_progress_line(self, rev):
		if rev != self.last_rev:
			self.print_progress_message("Processing revision %s" % rev, end='\r')
			self.last_rev = rev
		return

	def print_last_progress_line(self):
		elapsed = datetime.timedelta(seconds=time.monotonic() - self.start_time)
		self.print_progress_message("Processed %d revisions in %s" % (self.total_revisions, str(elapsed)))
		return

	### load function loads SVN dump from the given 'revision_reader' generator function
	# If 'log_dump' is set in options, the headers and revisions are printed to options.logfile
	def load(self, revision_reader):
		log_file = getattr(self.options, 'log_file', sys.stdout)
		log_dump = getattr(self.options, 'log_dump', True)
		end_revision = getattr(self.options, 'end_revision', None)
		verify_data_hash = getattr(self.options, 'verify_data_hash', True)

		if end_revision is not None:
			end_revision = int(end_revision)

		self.total_revisions = 0
		self.last_progress_time = 0.
		self.start_time = time.monotonic()

		rev = None
		try:
			for dump_revision in revision_reader.read_revisions(verify_data_hash):
				rev = dump_revision.rev
				
				self.update_progress(rev)

				if log_dump:
					dump_revision.print(log_file)

				self.total_revisions += 1

				if end_revision is not None and rev >= end_revision:
					break

				continue

			self.print_last_progress_line()

		except:
			if rev is not None:
				print("\nInterrupted at revision %s" % rev, file=sys.stderr)
			raise
		return self
