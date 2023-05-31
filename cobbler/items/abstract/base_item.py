"""
TODO
"""

import logging
import re
import uuid
from typing import TYPE_CHECKING, Any

from cobbler.decorator import LazyProperty

if TYPE_CHECKING:
    from cobbler.api import CobblerAPI


RE_OBJECT_NAME = re.compile(r"[a-zA-Z0-9_\-.:]*$")


class BaseItem:
    """
    A ``BaseItem`` is a serializable thing that can appear in a ``Collection``.
    """

    # Constants
    TYPE_NAME = "abstract"
    COLLECTION_TYPE = "abstract"

    def __init__(self, api: "CobblerAPI", **kwargs: Any):
        """
        TODO
        """
        self._ctime = 0.0
        self._mtime = 0.0
        self._uid = uuid.uuid4().hex
        self._name = ""
        self._comment = ""

        self.logger = logging.getLogger()
        self.api = api

    def __eq__(self, other: Any) -> bool:
        """
        Comparison based on the uid for our items.

        :param other: The other Item to compare.
        :return: True if uid is equal, otherwise false.
        """
        if isinstance(other, BaseItem):
            return self._uid == other.uid
        return False

    def __hash__(self):
        """
        Hash table for Items.
        Requires special handling if the uid value changes and the Item
        is present in set, frozenset, and dict types.

        :return: hash(uid).
        """
        return hash(self._uid)
    
    @property
    def uid(self) -> str:
        """
        The uid is the internal unique representation of a Cobbler object. It should never be used twice, even after an
        object was deleted.

        :getter: The uid for the item. Should be unique across a running Cobbler instance.
        :setter: The new uid for the object. Should only be used by the Cobbler Item Factory.
        """
        return self._uid

    @uid.setter
    def uid(self, uid: str) -> None:
        """
        Setter for the uid of the item.

        :param uid: The new uid.
        """
        if self._uid != uid and self.COLLECTION_TYPE != Item.COLLECTION_TYPE:
            name = self.name.lower()
            collection = self.api.get_items(self.COLLECTION_TYPE)
            with collection.lock:
                if collection.get(name) is not None:
                    # Changing the hash of an object requires special handling.
                    collection.listing.pop(name)
                    self._uid = uid
                    collection.listing[name] = self  # type: ignore
                    return
        self._uid = uid

    @property
    def ctime(self) -> float:
        """
        Property which represents the creation time of the object.

        :getter: The float which can be passed to Python time stdlib.
        :setter: Should only be used by the Cobbler Item Factory.
        """
        return self._ctime

    @ctime.setter
    def ctime(self, ctime: float) -> None:
        """
        Setter for the ctime property.

        :param ctime: The time the object was created.
        :raises TypeError: In case ``ctime`` was not of type float.
        """
        if not isinstance(ctime, float):  # type: ignore
            raise TypeError("ctime needs to be of type float")
        self._ctime = ctime

    @property
    def mtime(self) -> float:
        """
        Represents the last modification time of the object via the API. This is not updated automagically.

        :getter: The float which can be fed into a Python time object.
        :setter: The new time something was edited via the API.
        """
        return self._mtime

    @mtime.setter
    def mtime(self, mtime: float) -> None:
        """
        Setter for the modification time of the object.

        :param mtime: The new modification time.
        """
        if not isinstance(mtime, float):  # type: ignore
            raise TypeError("mtime needs to be of type float")
        self._mtime = mtime

    @property
    def name(self) -> str:
        """
        Property which represents the objects name.

        :getter: The name of the object.
        :setter: Updating this has broad implications. Please try to use the ``rename()`` functionality from the
                 corresponding collection.
        """
        return self._name

    @name.setter
    def name(self, name: str) -> None:
        """
        The objects name.

        :param name: object name string
        :raises TypeError: In case ``name`` was not of type str.
        :raises ValueError: In case there were disallowed characters in the name.
        """
        if not isinstance(name, str):  # type: ignore
            raise TypeError("name must of be type str")
        if not RE_OBJECT_NAME.match(name):
            raise ValueError(f"Invalid characters in name: '{name}'")
        self._name = name

    @LazyProperty
    def comment(self) -> str:
        """
        For every object you are able to set a unique comment which will be persisted on the object.

        :getter: The comment or an emtpy string.
        :setter: The new comment for the item.
        """
        return self._comment

    @comment.setter
    def comment(self, comment: str) -> None:
        """
        Setter for the comment of the item.

        :param comment: The new comment. If ``None`` the comment will be set to an emtpy string.
        """
        self._comment = comment
