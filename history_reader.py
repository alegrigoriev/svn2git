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

### load function loads revisions from the given revision reader
# If optional 'options.log_file' file descriptor is supplied, the headers and revisions are printed to it
def load_history(revision_reader, options=None):

	if getattr(options, 'log_dump', True):
		logfile = getattr(options, 'log_file', sys.stdout)
	else:
		logfile = None
	verify_data_hash = getattr(options, 'verify_data_hash', True)

	for dump_revision in revision_reader.read_revisions(verify_data_hash):

		if logfile:
			dump_revision.print(logfile)
		continue
	return

