"""
TODO
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from cobbler.cexceptions import CX
from cobbler.decorator import LazyProperty
from cobbler.items.abstract.item import Item

if TYPE_CHECKING:
    from cobbler.api import CobblerAPI
    from cobbler.cobbler_collections.collection import ITEM_UNION
    from cobbler.items.distro import Distro
    from cobbler.items.profile import Profile
    from cobbler.items.system import System
    from cobbler.items.menu import Menu
    from cobbler.settings import Settings


class InheritableItem(Item):
    """
    An Item that can have both parents and children.
    """

    # Item types dependencies.
    # Used to determine descendants and cache invalidation.
    # Format: {"Item Type": [("Dependent Item Type", "Dependent Type attribute"), ..], [..]}
    TYPE_DEPENDENCIES: Dict[str, List[Tuple[str, str]]] = {
        "package": [
            ("mgmtclass", "packages"),
        ],
        "file": [
            ("mgmtclass", "files"),
            ("image", "file"),
        ],
        "mgmtclass": [
            ("distro", "mgmt_classes"),
            ("profile", "mgmt_classes"),
            ("system", "mgmt_classes"),
        ],
        "repo": [
            ("profile", "repos"),
        ],
        "distro": [
            ("profile", "distro"),
        ],
        "menu": [
            ("menu", "parent"),
            ("image", "menu"),
            ("profile", "menu"),
        ],
        "profile": [
            ("profile", "parent"),
            ("system", "profile"),
        ],
        "image": [
            ("system", "image"),
        ],
        "system": [],
    }

    # Defines a logical hierarchy of Item Types.
    # Format: {"Item Type": [("Previous level Type", "Attribute to go to the previous level",), ..],
    #                       [("Next level Item Type", "Attribute to move from the next level"), ..]}
    LOGICAL_INHERITANCE: Dict[
        str, Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]
    ] = {
        "distro": (
            [],
            [
                ("profile", "distro"),
            ],
        ),
        "profile": (
            [
                ("distro", "distro"),
            ],
            [
                ("system", "profile"),
            ],
        ),
        "image": (
            [],
            [
                ("system", "image"),
            ],
        ),
        "system": ([("image", "image"), ("profile", "profile")], []),
    }

    def __init__(self, api: "CobblerAPI", is_subobject: bool = False, **kwargs: Any):
        super().__init__(api, is_subobject, **kwargs)
        self._children: List[str] = []
        self._is_subobject = is_subobject

    @LazyProperty
    def parent(self) -> Optional[Union["System", "Profile", "Distro", "Menu"]]:
        """
        This property contains the name of the parent of an object. In case there is not parent this return
        None.

        :getter: Returns the parent object or None if it can't be resolved via the Cobbler API.
        :setter: The name of the new logical parent.
        """
        if self._parent == "":
            return None
        return self.api.get_items(self.COLLECTION_TYPE).get(self._parent)  # type: ignore

    @parent.setter
    def parent(self, parent: str) -> None:
        """
        Set the parent object for this object.

        :param parent: The new parent object. This needs to be a descendant in the logical inheritance chain.
        """
        if not isinstance(parent, str):  # type: ignore
            raise TypeError('Property "parent" must be of type str!')
        if not parent:
            self._parent = ""
            return
        if parent == self.name:
            # check must be done in two places as setting parent could be called before/after setting name...
            raise CX("self parentage is weird")
        found = self.api.get_items(self.COLLECTION_TYPE).get(parent)
        if found is None:
            raise CX(f'profile "{parent}" not found, inheritance not possible')
        self._parent = parent
        self.depth = found.depth + 1

    @LazyProperty
    def get_parent(self) -> str:
        """
        This method returns the name of the parent for the object. In case there is not parent this return
        empty string.
        """
        return self._parent

    def get_conceptual_parent(self) -> Optional["ITEM_UNION"]:
        """
        The parent may just be a superclass for something like a subprofile. Get the first parent of a different type.

        :return: The first item which is conceptually not from the same type.
        """
        if self is None:  # type: ignore
            return None

        curr_obj = self
        next_obj = curr_obj.parent
        while next_obj is not None:
            curr_obj = next_obj
            next_obj = next_obj.parent

        if curr_obj.TYPE_NAME in curr_obj.LOGICAL_INHERITANCE:
            for prev_level in curr_obj.LOGICAL_INHERITANCE[curr_obj.TYPE_NAME][0]:
                prev_level_type = prev_level[0]
                prev_level_name = getattr(curr_obj, "_" + prev_level[1])
                if prev_level_name is not None and prev_level_name != "":
                    prev_level_item = self.api.find_items(
                        prev_level_type, name=prev_level_name, return_list=False
                    )
                    if prev_level_item is not None and not isinstance(
                        prev_level_item, list
                    ):
                        return prev_level_item
        return None

    @property
    def logical_parent(self) -> Any:
        """
        This property contains the name of the logical parent of an object. In case there is not parent this return
        None.

        :getter: Returns the parent object or None if it can't be resolved via the Cobbler API.
        :setter: The name of the new logical parent.
        """
        parent = self.parent
        if parent is None:
            return self.get_conceptual_parent()
        return parent

    @property
    def children(self) -> List["ITEM_UNION"]:
        """
        The list of logical children of any depth.
        :getter: An empty list in case of items which don't have logical children.
        :setter: Replace the list of children completely with the new provided one.
        """
        results: List[Any] = []
        list_items = self.api.get_items(self.COLLECTION_TYPE)
        for obj in list_items:
            if obj.get_parent == self._name:
                results.append(obj)
        return results

    def tree_walk(self) -> List["ITEM_UNION"]:
        """
        Get all children related by parent/child relationship.
        :return: The list of children objects.
        """
        results: List[Any] = []
        for child in self.children:
            results.append(child)
            results.extend(child.tree_walk())

        return results

    @property
    def descendants(self) -> List["ITEM_UNION"]:
        """
        Get objects that depend on this object, i.e. those that would be affected by a cascading delete, etc.

        .. note:: This is a read only property.

        :getter: This is a list of all descendants. May be empty if none exist.
        """
        childs = self.tree_walk()
        results = set(childs)
        childs.append(self)  # type: ignore
        for child in childs:
            for item_type in Item.TYPE_DEPENDENCIES[child.COLLECTION_TYPE]:
                dep_type_items = self.api.find_items(
                    item_type[0], {item_type[1]: child.name}, return_list=True
                )
                if dep_type_items is None or not isinstance(dep_type_items, list):
                    raise ValueError("Expected list to be returned by find_items")
                results.update(dep_type_items)
                for dep_item in dep_type_items:
                    results.update(dep_item.descendants)
        return list(results)

    @LazyProperty
    def is_subobject(self) -> bool:
        """
        Weather the object is a subobject of another object or not.

        :getter: True in case the object is a subobject, False otherwise.
        :setter: Sets the value. If this is not a bool, this will raise a ``TypeError``.
        """
        return self._is_subobject

    @is_subobject.setter
    def is_subobject(self, value: bool) -> None:
        """
        Setter for the property ``is_subobject``.

        :param value: The boolean value whether this is a subobject or not.
        :raises TypeError: In case the value was not of type bool.
        """
        if not isinstance(value, bool):  # type: ignore
            raise TypeError(
                "Field is_subobject of object item needs to be of type bool!"
            )
        self._is_subobject = value

    def grab_tree(self) -> List[Union["Item", "Settings"]]:
        """
        Climb the tree and get every node.

        :return: The list of items with all parents from that object upwards the tree. Contains at least the item
                 itself and the settings of Cobbler.
        """
        results: List[Union["Item", "Settings"]] = [self]
        parent = self.logical_parent
        while parent is not None:
            results.append(parent)
            parent = parent.logical_parent
            # FIXME: Now get the object and check its existence
        results.append(self.api.settings())
        self.logger.debug(
            "grab_tree found %s children (including settings) of this object",
            len(results),
        )
        return results
