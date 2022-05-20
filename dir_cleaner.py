# Copyright 2022 Jonathan L. Komar
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
# of the Software, and to permit persons to whom the Software is furnished to do
# so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from pathlib import Path
import time
from os import scandir, DirEntry, stat_result
from typing import List,Iterator,Tuple
from dataclasses import dataclass
from collections import deque
import sys
from fpinpy import Result, IniConfigReader, SinglyLinkedList
from fpinpy import MapUtilities as Maps
from inspect import Signature, Parameter
import re

import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def make_signature(names):
    return Signature(Parameter(name, Parameter.POSITIONAL_OR_KEYWORD) for name in names)

@dataclass
class Project:
    path: Path
    branches: List

class ConfigUnit:
    __signature__ = make_signature(["search_path", "match_pattern", "exclude_pattern", "keep_duration"])
    def __init__(self, *args, **kwargs):
        bound = self.__class__.__signature__.bind(*args, **kwargs)
        for name, value in bound.arguments.items():
            setattr(self, name, value)
    def __repr__(self):
        return f"{self.__class__.__name__}({self.__dict__})"

class ConfigContainer:
    """Logic pertaining to extracting ConfigUnits from extensible configuration format.

        Rules:
            Each key in any section may only appear once (TODO undefined otherwise).
            The key word is the name that relates a SearchPath to all other
            keys in other coniguration sections.

        In other words, the plurality of configuration (the support of >1
        search paths) is why this abstraction is here.
    """
    __signature__ = make_signature(["search_paths", "match_patterns", "exclude_patterns", "keep_durations"])
    # each is a list of tuple (key, value)
    def __init__(self, *args, **kwargs):
        bound = self.__class__.__signature__.bind(*args, **kwargs)
        for name, value in bound.arguments.items():
            setattr(self, name, value)
        self.configUnits = self._split_into_units()

    def _split_into_units(self) -> SinglyLinkedList[ConfigUnit]:
        """ For each searchpath key, establish relation to other config section keys using key name

            Fail if anything doesn't work out.
        """
        matches_dict = self.match_patterns.foldLeft({}, lambda x: lambda y: { **x, **{ y[0]: y[1] }})
        excludes_dict = self.exclude_patterns.foldLeft({}, lambda x: lambda y: { **x, **{ y[0]: y[1] }})
        durations_dict = self.keep_durations.foldLeft({}, lambda x: lambda y: { **x, **{ y[0]: y[1] }})

        # TODO validate existence of path on fs
        rConfigUnits = self.search_paths\
            .map(lambda path: Maps.getMapValue(path[0], matches_dict)\
                .flatMap(lambda matchPat: Maps.getMapValue(path[0], excludes_dict)\
                .flatMap(lambda excludePat: Maps.getMapValue(path[0], durations_dict).map(lambda dur: int(dur))\
                .map(lambda duration: ConfigUnit(
                                            search_path=path[1],
                                            match_pattern=matchPat,
                                            exclude_pattern=excludePat,
                                            keep_duration=duration)))))
        # rConfigUnits is not a list of List[Result[ConfigUnit]]. Convert to Result[List]
        # Use sequence to propogate Result to caller
        return SinglyLinkedList.sequence(rConfigUnits, ignoreFailure=False)

    def __repr__(self):
        return f"ConfigContainer({self.__dict__})"

    def get_unit(self):
        """Return a ConfigUnit per SearchPath

            TODO: Ideally, this would return a stream of ConfigUnit that
            threads can pull from.
        """

    def get_config_units(self):
        return self.configUnits

@dataclass
class TopMatch:
    path: DirEntry
    level: int

class TopDir():
    def __init__(self, path: str):
        self.path = Path(path)

    def get_dir_occurrences_by(self, name: re.Pattern, path: Path, level=0): # Iterator[DirEntry]
        """ Uses scandir over walk because it is 
            a true iterator that returns a DirEntry obj
            populated by the sys call. Faster.
        """
        """Recursively yield DirEntry objects for given directory."""
        for entry in scandir(path):
            if not entry.name.startswith('.') and entry.is_dir(follow_symlinks=False):
                yield from self.get_dir_occurrences_by(name, entry.path, level=level+1)
                if name.match(entry.name):
                    yield TopMatch(entry, level)
    def get_contained_dirs(self) -> Iterator[DirEntry]:
        for entry in scandir(self.path):
            yield entry

    def delete(self):
        tmp_lifo = deque()
        out_lifo = deque()

        tmp_lifo.append(self.path)

        while tmp_lifo:
            entry = tmp_lifo.pop()
            if entry.is_dir():
                 out_lifo.append(entry)
                 for p in entry.iterdir():
                    tmp_lifo.append(p)
            else:
                out_lifo.append(entry)
        while out_lifo:
            path = out_lifo.pop()
            logger.debug(f"Deleting {path}")
            path.rmdir() if path.is_dir() else path.unlink()

    def __str__(self):
        return f"TopDir({self.path})"

    @staticmethod
    def delete_entries(entries):
       for entry in entries:
        td = TopDir(entry)
        td.delete()

def safe_head(alist):
    try:
        return Result.of(alist.head())
    except Exception as e:
        return Result.failure(e)

def valid_path(path):
    try:
        return Result.of(path.resolve(strict=True))
    except Exception as e:
        return Result.failure(e)

class ActionUnit():
    """ Abstraction layer for actions 

        TODO: separate more the of the logic from the actions for testing
    """
    def __init__(self, config_unit):
        self.search_dir =  config_unit.search_path
        self.match_pattern = re.compile(config_unit.match_pattern)
        self.keep_predicate = self.gen_predicate(re.compile(config_unit.exclude_pattern), config_unit.keep_duration)

    def gen_predicate(self, pattern, duration):
        def predicate(dir_entry) -> bool:
            sec_in_a_day = 86400
            days_allowed = duration
            duration_limit = time.time() - (days_allowed * sec_in_a_day)
            logger.debug(f"Applying predicate to {dir_entry}")
            return pattern.match(dir_entry.name) or dir_entry.stat().st_ctime > duration_limit
        return predicate

    def action_script(self, search_dir, match_pattern, keep_predicate):
        """ Use search directory as top-level dir.

            Find all paths that match the match_pattern.
            Test each match against the keep_predicate.
            Remove all matches (deletion candidates) that fail 
            to meet the criterion of the keep_predicate.

            This can be run concurrently.

            Inputs:
                search_dir: the path to consider
                match_pattern: pattern to match against a path under search_dir
                keep_predicate: boolean function to negate the match_pattern e.g. exclude pattern + keep duration
        """
        top = TopDir(Path(search_dir))
        match_dirs = top.get_dir_occurrences_by(name=match_pattern, path=top.path)

        deletion_candidates = []

        for match_dir in match_dirs:
            matches = TopDir(match_dir.path).get_contained_dirs()
            for aMatch in matches:
                logger.debug(f"Processing: {aMatch}")
                if keep_predicate(aMatch):
                    logger.debug(f"Keeping: {aMatch}")
                else:
                    logger.debug(f"Adding deletion candidate: {aMatch}")
                    deletion_candidates.append(aMatch)

        TopDir.delete_entries(deletion_candidates)

    def __call__(self):
        self.action_script(self.search_dir, self.match_pattern, self.keep_predicate)

if __name__ == '__main__':

    safe_argv = SinglyLinkedList.list(*sys.argv)
    safe_argv = safe_argv.drop(1)

    rReader = safe_head(safe_argv)\
        .mapFailure("No argument passed. Requires path to configuration.")\
        .map(lambda p: Path(p))\
        .flatMap(lambda path: valid_path(path))\
        .flatMap(lambda path: IniConfigReader.of(path))

    rConfigContainer = rReader\
                        .flatMap(lambda parser: parser.getSection("SearchPaths")\
                        .flatMap(lambda paths: parser.getSection("MatchPatterns")\
                        .flatMap(lambda matchPats: parser.getSection("ExcludePatterns")\
                        .flatMap(lambda excludePats: parser.getSection("KeepDurations")\
                        .map(lambda durations: ConfigContainer(paths, matchPats, excludePats, durations) )))))

    rConfigUnits = rConfigContainer.flatMap(lambda conf: conf.get_config_units())

    # END OF FUNCTIONAL CORE
    rConfigUnits\
        .forEachOrFail(lambda x: logger.debug(x))\
        .forEach(lambda error_msg: logger.error(f"Could not proceed because: {error_msg}"))
    rActionUnits = rConfigUnits.getOrElse(SinglyLinkedList.list()).map(lambda x: ActionUnit(x))
    rActionUnits\
        .forEach(lambda action: action())
