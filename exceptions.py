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

class Exception_svn_parse(Exception):
	def __init__(self, text, obj = None):
		self.strerror = text
		self.obj = obj
		return

class Exception_history_parse(Exception):
	def __init__(self, text, obj = None):
		self.strerror = text
		self.obj = obj
		return

class Exception_cfg_parse(Exception):
	def __init__(self, text, obj = None):
		self.strerror = text
		self.obj = obj
		return
