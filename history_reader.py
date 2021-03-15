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

### load function loads revisions from the given revision reader
# If optional 'options.log_file' file descriptor is supplied, the headers and revisions are printed to it
def load_history(revision_reader, options=None):
	quiet = getattr(options, 'quiet', False)
	progress = getattr(options, 'progress', 1.)
	end_revision = getattr(options, 'end_revision', None)

	if end_revision is not None:
		end_revision = int(end_revision)

	if getattr(options, 'log_dump', True):
		logfile = getattr(options, 'log_file', sys.stdout)
	else:
		logfile = None

	total_revisions = 0
	last_progress_time = 0.
	start_time = time.monotonic()
	last_rev = None

	verify_data_hash = getattr(options, 'verify_data_hash', True)

	def print_progress_message(msg, end=None):
		if not quiet:
			print(msg, end=end, file=sys.stderr)
		return

	def update_progress(rev):
		nonlocal last_progress_time
		if progress is not None and time.monotonic() - last_progress_time >= progress:
			print_progress_line(rev)
			last_progress_time = time.monotonic()
		return

	def print_progress_line(rev):
		nonlocal last_rev
		if rev != last_rev:
			print_progress_message("Processing revision %s" % rev, end='\r')
			last_rev = rev
		return

	def print_last_progress_line():
		nonlocal total_revisions
		elapsed = datetime.timedelta(seconds=time.monotonic() - start_time)
		print_progress_message("Processed %d revisions in %s" % (total_revisions, str(elapsed)))
		return

	rev = None
	try:
		for dump_revision in revision_reader.read_revisions(verify_data_hash):
			rev = dump_revision.rev

			update_progress(rev)

			if logfile:
				dump_revision.print(logfile)
			total_revisions += 1

			if end_revision is not None and rev >= end_revision:
				break
			continue

		print_last_progress_line()

	except:
		if rev is not None:
			print("\nInterrupted at revision %s" % rev, file=sys.stderr)
		raise
	return

