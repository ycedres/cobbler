"""
Cobbler module that contains the code for a generic Cobbler item.

Changelog:

V3.4.0 (unreleased):
    * Split into multiple "Item" and "InheritableItem"
    * (Re-)Added Cache implementation with the following new methods and properties:
        * ``cache``
        * ``inmemery``
        * ``clean_cache()``
    * Overhauled the parent/child system:
        * ``children`` is now inside ``item.py``.
        * ``tree_walk()`` was added.
        * ``logical_parent`` was added.
        * ``get_parent()`` was added which returns the internal reference that is used to return the object of the
          ``parent`` property.
V3.3.4 (unreleased):
    * No changes
V3.3.3:
    * Added:
        * ``grab_tree``
V3.3.2:
    * No changes
V3.3.1:
    * No changes
V3.3.0:
    * This release switched from pure attributes to properties (getters/setters).
    * Added:
        * ``depth``: int
        * ``comment``: str
        * ``owners``: Union[list, str]
        * ``mgmt_classes``: Union[list, str]
        * ``mgmt_classes``: Union[dict, str]
        * ``conceptual_parent``: Union[distro, profile]
    * Removed:
        * collection_mgr: collection_mgr
        * Remove unreliable caching:
            * ``get_from_cache()``
            * ``set_cache()``
            * ``remove_from_cache()``
    * Changed:
        * Constructor: Takes an instance of ``CobblerAPI`` instead of ``CollectionManager``.
        * ``children``: dict -> list
        * ``ctime``: int -> float
        * ``mtime``: int -> float
        * ``uid``: str
        * ``kernel_options``: dict -> Union[dict, str]
        * ``kernel_options_post``: dict -> Union[dict, str]
        * ``autoinstall_meta``: dict -> Union[dict, str]
        * ``fetchable_files``: dict -> Union[dict, str]
        * ``boot_files``: dict -> Union[dict, str]
V3.2.2:
    * No changes
V3.2.1:
    * No changes
V3.2.0:
    * No changes
V3.1.2:
    * No changes
V3.1.1:
    * No changes
V3.1.0:
    * No changes
V3.0.1:
    * No changes
V3.0.0:
    * Added:
        * ``collection_mgr``: collection_mgr
        * ``kernel_options``: dict
        * ``kernel_options_post``: dict
        * ``autoinstall_meta``: dict
        * ``fetchable_files``: dict
        * ``boot_files``: dict
        * ``template_files``: dict
        * ``name``: str
        * ``last_cached_mtime``: int
    * Changed:
        * Rename: ``cached_datastruct`` -> ``cached_dict``
    * Removed:
        * ``config``
V2.8.5:
    * Added:
        * ``config``: ?
        * ``settings``: settings
        * ``is_subobject``: bool
        * ``parent``: Union[distro, profile]
        * ``children``: dict
        * ``log_func``: collection_mgr.api.log
        * ``ctime``: int
        * ``mtime``: int
        * ``uid``: str
        * ``last_cached_mtime``: int
        * ``cached_datastruct``: str
"""

import copy
import enum
import fnmatch
import pprint
import uuid
from abc import abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

import yaml

from cobbler import enums, utils
from cobbler.cexceptions import CX
from cobbler.decorator import InheritableDictProperty, InheritableProperty, LazyProperty
from cobbler.items.abstract.base_item import BaseItem
from cobbler.items.abstract.item_cache import ItemCache
from cobbler.utils import input_converters

if TYPE_CHECKING:
    from cobbler.api import CobblerAPI

class Item(BaseItem):
    """
    An Item is a serializable thing that can appear in a Collection.
    """


    # Constants
    TYPE_NAME = "generic"
    COLLECTION_TYPE = "generic"

    @classmethod
    def __find_compare(
        cls,
        from_search: Union[str, List[Any], Dict[Any, Any], bool],
        from_obj: Union[str, List[Any], Dict[Any, Any], bool],
    ) -> bool:
        """
        Only one of the two parameters shall be given in this method. If you give both ``from_obj`` will be preferred.

        :param from_search: Tries to parse this str in the format as a search result string.
        :param from_obj: Tries to parse this str in the format of an obj str.
        :return: True if the comparison succeeded, False otherwise.
        :raises TypeError: In case the type of one of the two variables is wrong or could not be converted
                           intelligently.
        """
        if isinstance(from_obj, str):
            # FIXME: fnmatch is only used for string to string comparisons which should cover most major usage, if
            #        not, this deserves fixing
            from_obj_lower = from_obj.lower()
            from_search_lower = from_search.lower()  # type: ignore
            # It's much faster to not use fnmatch if it's not needed
            if (
                "?" not in from_search_lower
                and "*" not in from_search_lower
                and "[" not in from_search_lower
            ):
                match = from_obj_lower == from_search_lower  # type: ignore
            else:
                match = fnmatch.fnmatch(from_obj_lower, from_search_lower)  # type: ignore
            return match  # type: ignore

        if isinstance(from_search, str):
            if isinstance(from_obj, list):
                from_search = input_converters.input_string_or_list(from_search)
                for list_element in from_search:
                    if list_element not in from_obj:
                        return False
                return True
            if isinstance(from_obj, dict):
                from_search = input_converters.input_string_or_dict(
                    from_search, allow_multiples=True
                )
                for dict_key in list(from_search.keys()):  # type: ignore
                    dict_value = from_search[dict_key]
                    if dict_key not in from_obj:
                        return False
                    if not (dict_value == from_obj[dict_key]):
                        return False
                return True
            if isinstance(from_obj, bool):  # type: ignore
                inp = from_search.lower() in ["true", "1", "y", "yes"]
                if inp == from_obj:
                    return True
                return False

        raise TypeError(f"find cannot compare type: {type(from_obj)}")

    @staticmethod
    def __is_dict_key(name: str) -> bool:
        """
        Whether the attribute is part of the item's to_dict or not

        :name: The attribute name.
        """
        return (
            name[:1] == "_"
            and "__" not in name
            and name
            not in {
                "_last_cached_mtime",
                "_cache",
                "_supported_boot_loaders",
                "_has_initialized",
                "_inmemory",
            }
        )

    def __init__(self, api: "CobblerAPI", is_subobject: bool = False, **kwargs: Any):
        """
        Constructor.  Requires a back reference to the CobblerAPI object.

        NOTE: is_subobject is used for objects that allow inheritance in their trees. This inheritance refers to
        conceptual inheritance, not Python inheritance. Objects created with is_subobject need to call their
        setter for parent immediately after creation and pass in a value of an object of the same type. Currently this
        is only supported for profiles. Subobjects blend their data with their parent objects and only require a valid
        parent name and a name for themselves, so other required options can be gathered from items further up the
        Cobbler tree.

                           distro
                               profile
                                    profile  <-- created with is_subobject=True
                                         system   <-- created as normal
                           image
                               system
                           menu
                               menu

        For consistency, there is some code supporting this in all object types, though it is only usable
        (and only should be used) for profiles at this time.  Objects that are children of
        objects of the same type (i.e. subprofiles) need to pass this in as True.  Otherwise, just
        use False for is_subobject and the parent object will (therefore) have a different type.

        The keyword arguments are used to seed the object. This is the preferred way over ``from_dict`` starting with
        Cobbler version 3.4.0.

        :param api: The Cobbler API object which is used for resolving information.
        :param is_subobject: See above extensive description.
        """
        super().__init__(api, **kwargs)
        # Prevent attempts to clear the to_dict cache before the object is initialized.
        self._has_initialized = False

        self._parent = ""
        self._depth = 0

        self._kernel_options: Union[Dict[Any, Any], str] = {}
        self._kernel_options_post: Union[Dict[Any, Any], str] = {}
        self._autoinstall_meta: Union[Dict[Any, Any], str] = {}
        self._fetchable_files: Union[Dict[Any, Any], str] = {}
        self._boot_files: Union[Dict[Any, Any], str] = {}
        self._template_files: Dict[str, Any] = {}
        self._last_cached_mtime = 0
        self._owners: Union[List[Any], str] = enums.VALUE_INHERITED
        self._cache: ItemCache = ItemCache(api)
        self._mgmt_classes: Union[List[Any], str] = enums.VALUE_INHERITED
        self._mgmt_parameters: Union[Dict[Any, Any], str] = {}
        self._inmemory = True

        if len(kwargs) > 0:
            kwargs.update({"is_subobject": is_subobject})
            Item.from_dict(self, kwargs)
        if self._uid == "":
            self._uid = uuid.uuid4().hex

        if not self._has_initialized:
            self._has_initialized = True

    def __setattr__(self, name: str, value: Any):
        """
        Intercepting an attempt to assign a value to an attribute.

        :name: The attribute name.
        :value: The attribute value.
        """
        if Item.__is_dict_key(name) and self._has_initialized:
            self.clean_cache(name)
        super().__setattr__(name, value)

    def _resolve(self, property_name: str) -> Any:
        """
        Resolve the ``property_name`` value in the object tree. This function traverses the tree from the object to its
        topmost parent and returns the first value that is not inherited. If the the tree does not contain a value the
        settings are consulted.

        :param property_name: The property name to resolve.
        :raises AttributeError: In case one of the objects try to inherit from a parent that does not have
                                ``property_name``.
        :return: The resolved value.
        """
        settings_name = property_name
        if property_name.startswith("proxy_url_"):
            property_name = "proxy"
        if property_name == "owners":
            settings_name = "default_ownership"
        attribute = "_" + property_name

        if not hasattr(self, attribute):
            raise AttributeError(
                f'{type(self)} "{self.name}" does not have property "{property_name}"'
            )

        attribute_value = getattr(self, attribute)
        settings = self.api.settings()

        if attribute_value == enums.VALUE_INHERITED:
            logical_parent = self.logical_parent
            if logical_parent is not None and hasattr(logical_parent, property_name):
                return getattr(logical_parent, property_name)
            if hasattr(settings, settings_name):
                return getattr(settings, settings_name)
            if hasattr(settings, f"default_{settings_name}"):
                return getattr(settings, f"default_{settings_name}")
            AttributeError(
                f'{type(self)} "{self.name}" inherits property "{property_name}", but neither its parent nor'
                f"settings have it"
            )

        return attribute_value

    def _resolve_enum(
        self, property_name: str, enum_type: Type[enums.ConvertableEnum]
    ) -> Any:
        """
        See :meth:`~cobbler.items.item.Item._resolve`
        """
        settings_name = property_name
        attribute = "_" + property_name

        if not hasattr(self, attribute):
            raise AttributeError(
                f'{type(self)} "{self.name}" does not have property "{property_name}"'
            )

        attribute_value = getattr(self, attribute)
        settings = self.api.settings()

        if (
            isinstance(attribute_value, enums.ConvertableEnum)
            and attribute_value.value == enums.VALUE_INHERITED
        ):
            logical_parent = self.logical_parent
            if logical_parent is not None and hasattr(logical_parent, property_name):
                return getattr(logical_parent, property_name)
            if hasattr(settings, settings_name):
                return enum_type.to_enum(getattr(settings, settings_name))
            if hasattr(settings, f"default_{settings_name}"):
                return enum_type.to_enum(getattr(settings, f"default_{settings_name}"))
            AttributeError(
                f'{type(self)} "{self.name}" inherits property "{property_name}", but neither its parent nor'
                "settings have it"
            )

        return attribute_value

    def _resolve_dict(self, property_name: str) -> Dict[str, Any]:
        """
        Merge the ``property_name`` dictionary of the object with the ``property_name`` of all its parents. The value
        of the child takes precedence over the value of the parent.

        :param property_name: The property name to resolve.
        :return: The merged dictionary.
        :raises AttributeError: In case the the the object had no attribute with the name :py:property_name: .
        """
        attribute = "_" + property_name

        if not hasattr(self, attribute):
            raise AttributeError(
                f'{type(self)} "{self.name}" does not have property "{property_name}"'
            )

        attribute_value = getattr(self, attribute)
        settings = self.api.settings()

        merged_dict: Dict[str, Any] = {}

        logical_parent = self.logical_parent
        if logical_parent is not None and hasattr(logical_parent, property_name):
            merged_dict.update(getattr(logical_parent, property_name))
        elif hasattr(settings, property_name):
            merged_dict.update(getattr(settings, property_name))

        if attribute_value != enums.VALUE_INHERITED:
            merged_dict.update(attribute_value)

        utils.dict_annihilate(merged_dict)
        return merged_dict

    @InheritableProperty
    def owners(self) -> List[Any]:
        """
        This is a feature which is related to the ownership module of Cobbler which gives only specific people access
        to specific records. Otherwise this is just a cosmetic feature to allow assigning records to specific users.

        .. warning:: This is never validated against a list of existing users. Thus you can lock yourself out of a
                     record.

        .. note:: This property can be set to ``<<inherit>>``.

        :getter: Return the list of users which are currently assigned to the record.
        :setter: The list of people which should be new owners. May lock you out if you are using the ownership
                 authorization module.
        """
        return self._resolve("owners")

    @owners.setter  # type: ignore[no-redef]
    def owners(self, owners: Union[str, List[Any]]):
        """
        Setter for the ``owners`` property.

        :param owners: The new list of owners. Will not be validated for existence.
        """
        if not isinstance(owners, (str, list)):  # type: ignore
            raise TypeError("owners must be str or list!")
        self._owners = input_converters.input_string_or_list(owners)

    @InheritableDictProperty
    def kernel_options(self) -> Dict[Any, Any]:
        """
        Kernel options are a space delimited list, like 'a=b c=d e=f g h i=j' or a dict.

        .. note:: This property can be set to ``<<inherit>>``.

        :getter: The parsed kernel options.
        :setter: The new kernel options as a space delimited list. May raise ``ValueError`` in case of parsing problems.
        """
        return self._resolve_dict("kernel_options")

    @kernel_options.setter  # type: ignore[no-redef]
    def kernel_options(self, options: Dict[str, Any]):
        """
        Setter for ``kernel_options``.

        :param options: The new kernel options as a space delimited list.
        :raises ValueError: In case the values set could not be parsed successfully.
        """
        try:
            self._kernel_options = input_converters.input_string_or_dict(
                options, allow_multiples=True
            )
        except TypeError as error:
            raise TypeError("invalid kernel options") from error

    @InheritableDictProperty
    def kernel_options_post(self) -> Dict[str, Any]:
        """
        Post kernel options are a space delimited list, like 'a=b c=d e=f g h i=j' or a dict.

        .. note:: This property can be set to ``<<inherit>>``.

        :getter: The dictionary with the parsed values.
        :setter: Accepts str in above mentioned format or directly a dict.
        """
        return self._resolve_dict("kernel_options_post")

    @kernel_options_post.setter  # type: ignore[no-redef]
    def kernel_options_post(self, options: Union[Dict[Any, Any], str]) -> None:
        """
        Setter for ``kernel_options_post``.

        :param options: The new kernel options as a space delimited list.
        :raises ValueError: In case the options could not be split successfully.
        """
        try:
            self._kernel_options_post = input_converters.input_string_or_dict(
                options, allow_multiples=True
            )
        except TypeError as error:
            raise TypeError("invalid post kernel options") from error

    @InheritableDictProperty
    def autoinstall_meta(self) -> Dict[Any, Any]:
        """
        A comma delimited list of key value pairs, like 'a=b,c=d,e=f' or a dict.
        The meta tags are used as input to the templating system to preprocess automatic installation template files.

        .. note:: This property can be set to ``<<inherit>>``.

        :getter: The metadata or an empty dict.
        :setter: Accepts anything which can be split by :meth:`~cobbler.utils.input_converters.input_string_or_dict`.
        """
        return self._resolve_dict("autoinstall_meta")

    @autoinstall_meta.setter  # type: ignore[no-redef]
    def autoinstall_meta(self, options: Dict[Any, Any]):
        """
        Setter for the ``autoinstall_meta`` property.

        :param options: The new options for the automatic installation meta options.
        :raises ValueError: If splitting the value does not succeed.
        """
        value = input_converters.input_string_or_dict(options, allow_multiples=True)
        self._autoinstall_meta = value

    @InheritableProperty
    def mgmt_classes(self) -> List[Any]:
        """
        Assigns a list of configuration management classes that can be assigned to any object, such as those used by
        Puppet's external_nodes feature.

        .. note:: This property can be set to ``<<inherit>>``.

        :getter: An empty list or the list of mgmt_classes.
        :setter: Will split this according to :meth:`~cobbler.utils.input_string_or_list`.
        """
        return self._resolve("mgmt_classes")

    @mgmt_classes.setter  # type: ignore[no-redef]
    def mgmt_classes(self, mgmt_classes: Union[List[Any], str]):
        """
        Setter for the ``mgmt_classes`` property.

        :param mgmt_classes: The new options for the management classes of an item.
        """
        if not isinstance(mgmt_classes, (str, list)):  # type: ignore
            raise TypeError("mgmt_classes has to be either str or list")
        self._mgmt_classes = input_converters.input_string_or_list(mgmt_classes)

    @InheritableDictProperty
    def mgmt_parameters(self) -> Dict[Any, Any]:
        """
        Parameters which will be handed to your management application (Must be a valid YAML dictionary)

        .. note:: This property can be set to ``<<inherit>>``.

        :getter: The mgmt_parameters or an empty dict.
        :setter: A YAML string which can be assigned to any object, this is used by Puppet's external_nodes feature.
        """
        return self._resolve_dict("mgmt_parameters")

    @mgmt_parameters.setter  # type: ignore[no-redef]
    def mgmt_parameters(self, mgmt_parameters: Union[str, Dict[Any, Any]]):
        """
        A YAML string which can be assigned to any object, this is used by Puppet's external_nodes feature.

        :param mgmt_parameters: The management parameters for an item.
        :raises TypeError: In case the parsed YAML isn't of type dict afterwards.
        """
        if not isinstance(mgmt_parameters, (str, dict)):  # type: ignore
            raise TypeError("mgmt_parameters must be of type str or dict")
        if isinstance(mgmt_parameters, str):
            if mgmt_parameters == enums.VALUE_INHERITED:
                self._mgmt_parameters = enums.VALUE_INHERITED
                return
            if mgmt_parameters == "":
                self._mgmt_parameters = {}
                return
            mgmt_parameters = yaml.safe_load(mgmt_parameters)
            if not isinstance(mgmt_parameters, dict):
                raise TypeError(
                    "Input YAML in Puppet Parameter field must evaluate to a dictionary."
                )
        self._mgmt_parameters = mgmt_parameters

    @LazyProperty
    def template_files(self) -> Dict[Any, Any]:
        """
        File mappings for built-in configuration management

        :getter: The dictionary with name-path key-value pairs.
        :setter: A dict. If not a dict must be a str which is split by
                 :meth:`~cobbler.utils.input_converters.input_string_or_dict`. Raises ``TypeError`` otherwise.
        """
        return self._template_files

    @template_files.setter
    def template_files(self, template_files: Union[str, Dict[Any, Any]]) -> None:
        """
        A comma seperated list of source=destination templates that should be generated during a sync.

        :param template_files: The new value for the template files which are used for the item.
        :raises ValueError: In case the conversion from non dict values was not successful.
        """
        try:
            self._template_files = input_converters.input_string_or_dict_no_inherit(
                template_files, allow_multiples=False
            )
        except TypeError as error:
            raise TypeError("invalid template files specified") from error

    @LazyProperty
    def boot_files(self) -> Dict[Any, Any]:
        """
        Files copied into tftpboot beyond the kernel/initrd

        :getter: The dictionary with name-path key-value pairs.
        :setter: A dict. If not a dict must be a str which is split by
                 :meth:`~cobbler.utils.input_converters.input_string_or_dict`. Raises ``TypeError`` otherwise.
        """
        return self._resolve_dict("boot_files")

    @boot_files.setter
    def boot_files(self, boot_files: Dict[Any, Any]) -> None:
        """
        A comma separated list of req_name=source_file_path that should be fetchable via tftp.

        .. note:: This property can be set to ``<<inherit>>``.

        :param boot_files: The new value for the boot files used by the item.
        """
        try:
            self._boot_files = input_converters.input_string_or_dict(
                boot_files, allow_multiples=False
            )
        except TypeError as error:
            raise TypeError("invalid boot files specified") from error

    @InheritableDictProperty
    def fetchable_files(self) -> Dict[Any, Any]:
        """
        A comma seperated list of ``virt_name=path_to_template`` that should be fetchable via tftp or a webserver

        .. note:: This property can be set to ``<<inherit>>``.

        :getter: The dictionary with name-path key-value pairs.
        :setter: A dict. If not a dict must be a str which is split by
                 :meth:`~cobbler.utils.input_converters.input_string_or_dict`. Raises ``TypeError`` otherwise.
        """
        return self._resolve_dict("fetchable_files")

    @fetchable_files.setter  # type: ignore[no-redef]
    def fetchable_files(self, fetchable_files: Union[str, Dict[Any, Any]]):
        """
        Setter for the fetchable files.

        :param fetchable_files: Files which will be made available to external users.
        """
        try:
            self._fetchable_files = input_converters.input_string_or_dict(
                fetchable_files, allow_multiples=False
            )
        except TypeError as error:
            raise TypeError("invalid fetchable files specified") from error

    @LazyProperty
    def depth(self) -> int:
        """
        This represents the logical depth of an object in the category of the same items. Important for the order of
        loading items from the disk and other related features where the alphabetical order is incorrect for sorting.

        :getter: The logical depth of the object.
        :setter: The new int for the logical object-depth.
        """
        return self._depth

    @depth.setter
    def depth(self, depth: int) -> None:
        """
        Setter for depth.

        :param depth: The new value for depth.
        """
        if not isinstance(depth, int):  # type: ignore
            raise TypeError("depth needs to be of type int")
        self._depth = depth

    def sort_key(self, sort_fields: List[Any]):
        """
        Convert the item to a dict and sort the data after specific given fields.

        :param sort_fields: The fields to sort the data after.
        :return: The sorted data.
        """
        data = self.to_dict()
        return [data.get(x, "") for x in sort_fields]

    def find_match(self, kwargs: Dict[str, Any], no_errors: bool = False) -> bool:
        """
        Find from a given dict if the item matches the kv-pairs.

        :param kwargs: The dict to match for in this item.
        :param no_errors: How strict this matching is.
        :return: True if matches or False if the item does not match.
        """
        # used by find() method in collection.py
        data = self.to_dict()
        for (key, value) in list(kwargs.items()):
            # Allow ~ to negate the compare
            if value is not None and value.startswith("~"):
                res = not self.find_match_single_key(data, key, value[1:], no_errors)
            else:
                res = self.find_match_single_key(data, key, value, no_errors)
            if not res:
                return False

        return True

    def find_match_single_key(
        self, data: Dict[str, Any], key: str, value: Any, no_errors: bool = False
    ) -> bool:
        """
        Look if the data matches or not. This is an alternative for ``find_match()``.

        :param data: The data to search through.
        :param key: The key to look for int the item.
        :param value: The value for the key.
        :param no_errors: How strict this matching is.
        :return: Whether the data matches or not.
        """
        # special case for systems
        key_found_already = False
        if "interfaces" in data:
            if key in [
                "cnames",
                "connected_mode",
                "if_gateway",
                "ipv6_default_gateway",
                "ipv6_mtu",
                "ipv6_prefix",
                "ipv6_secondaries",
                "ipv6_static_routes",
                "management",
                "mtu",
                "static",
                "mac_address",
                "ip_address",
                "ipv6_address",
                "netmask",
                "virt_bridge",
                "dhcp_tag",
                "dns_name",
                "static_routes",
                "interface_type",
                "interface_master",
                "bonding_opts",
                "bridge_opts",
                "interface",
            ]:
                key_found_already = True
                for (name, interface) in list(data["interfaces"].items()):
                    if value == name:
                        return True
                    if value is not None and key in interface:
                        if self.__find_compare(interface[key], value):
                            return True

        if key not in data:
            if not key_found_already:
                if not no_errors:
                    # FIXME: removed for 2.0 code, shouldn't cause any problems to not have an exception here?
                    # raise CX("searching for field that does not exist: %s" % key)
                    return False
            else:
                if value is not None:  # FIXME: new?
                    return False

        if value is None:
            return True
        return self.__find_compare(value, data[key])

    def dump_vars(
        self, formatted_output: bool = True, remove_dicts: bool = False
    ) -> Union[Dict[str, Any], str]:
        """
        Dump all variables.

        :param formatted_output: Whether to format the output or not.
        :param remove_dicts: If True the dictionaries will be put into str form.
        :return: The raw or formatted data.
        """
        raw = utils.blender(self.api, remove_dicts, self)  # type: ignore
        if formatted_output:
            return pprint.pformat(raw)
        return raw

    def check_if_valid(self) -> None:
        """
        Raise exceptions if the object state is inconsistent.

        :raises CX: In case the name of the item is not set.
        """
        if not self.name:
            raise CX("Name is required")

    @abstractmethod
    def make_clone(self) -> "ITEM":  # type: ignore
        """
        Must be defined in any subclass
        """
        raise NotImplementedError("Must be implemented in a specific Item")

    @classmethod
    def _remove_depreacted_dict_keys(cls, dictionary: Dict[Any, Any]) -> None:
        """
        This method does remove keys which should not be deserialized and are only there for API compatibility in
        :meth:`~cobbler.items.item.Item.to_dict`.

        :param dictionary: The dict to update
        """
        if "ks_meta" in dictionary:
            dictionary.pop("ks_meta")
        if "kickstart" in dictionary:
            dictionary.pop("kickstart")
        if "children" in dictionary:
            dictionary.pop("children")

    def from_dict(self, dictionary: Dict[Any, Any]) -> None:
        """
        Modify this object to take on values in ``dictionary``.

        :param dictionary: This should contain all values which should be updated.
        :raises AttributeError: In case during the process of setting a value for an attribute an error occurred.
        :raises KeyError: In case there were keys which could not be set in the item dictionary.
        """
        self._remove_depreacted_dict_keys(dictionary)
        if len(dictionary) == 0:
            return
        old_has_initialized = self._has_initialized
        self._has_initialized = False
        result = copy.deepcopy(dictionary)
        for key in dictionary:
            lowered_key = key.lower()
            # The following also works for child classes because self is a child class at this point and not only an
            # Item.
            if hasattr(self, "_" + lowered_key):
                try:
                    setattr(self, lowered_key, dictionary[key])
                except AttributeError as error:
                    raise AttributeError(
                        f'Attribute "{lowered_key}" could not be set!'
                    ) from error
                result.pop(key)
        self._has_initialized = old_has_initialized
        self.clean_cache()
        if len(result) > 0:
            raise KeyError(
                f"The following keys supplied could not be set: {result.keys()}"
            )

    def to_dict(self, resolved: bool = False) -> Dict[Any, Any]:
        """
        This converts everything in this object to a dictionary.

        :param resolved: If this is True, Cobbler will resolve the values to its final form, rather than give you the
                     objects raw value.
        :return: A dictionary with all values present in this object.
        """
        if not self.inmemory:
            self.deserialize()
        cached_result = self.cache.get_dict_cache(resolved)
        if cached_result is not None:
            return cached_result

        value: Dict[str, Any] = {}
        for key, key_value in self.__dict__.items():
            if self.__is_dict_key(key):
                new_key = key[1:].lower()
                if isinstance(key_value, enum.Enum):
                    if resolved:
                        value[new_key] = getattr(self, new_key).value
                    else:
                        value[new_key] = key_value.value
                elif new_key == "interfaces":
                    # This is the special interfaces dict. Lets fix it before it gets to the normal process.
                    serialized_interfaces = {}
                    interfaces = key_value
                    for interface_key in interfaces:
                        serialized_interfaces[interface_key] = interfaces[
                            interface_key
                        ].to_dict(resolved)
                    value[new_key] = serialized_interfaces
                elif isinstance(key_value, list):
                    value[new_key] = copy.deepcopy(key_value)  # type: ignore
                elif isinstance(key_value, dict):
                    if resolved:
                        value[new_key] = getattr(self, new_key)
                    else:
                        value[new_key] = copy.deepcopy(key_value)  # type: ignore
                elif (
                    isinstance(key_value, str)
                    and key_value == enums.VALUE_INHERITED
                    and resolved
                ):
                    value[new_key] = getattr(self, key[1:])
                else:
                    value[new_key] = key_value
        if "autoinstall" in value:
            value.update({"kickstart": value["autoinstall"]})  # type: ignore
        if "autoinstall_meta" in value:
            value.update({"ks_meta": value["autoinstall_meta"]})
        self.cache.set_dict_cache(value, resolved)
        return value

    def serialize(self) -> Dict[str, Any]:
        """
        This method is a proxy for :meth:`~cobbler.items.item.Item.to_dict` and contains additional logic for
        serialization to a persistent location.

        :return: The dictionary with the information for serialization.
        """
        keys_to_drop = [
            "kickstart",
            "ks_meta",
            "remote_grub_kernel",
            "remote_grub_initrd",
        ]
        result = self.to_dict()
        for key in keys_to_drop:
            result.pop(key, "")
        return result

    def deserialize(self) -> None:
        """
        Deserializes the object itself and, if necessary, recursively all the objects it depends on.
        """

        def deserialize_ancestor(ancestor_item_type: str, ancestor_name: str):
            if ancestor_name not in {"", enums.VALUE_INHERITED}:
                ancestor = self.api.get_items(ancestor_item_type).get(ancestor_name)
                if ancestor is not None and not ancestor.inmemory:
                    ancestor.deserialize()

        item_dict = self.api.deserialize_item(self)
        if item_dict["inmemory"]:
            for ancestor_item_type, ancestor_deps in Item.TYPE_DEPENDENCIES.items():
                for ancestor_dep in ancestor_deps:
                    if self.TYPE_NAME == ancestor_dep[0]:
                        attr_name = ancestor_dep[1]
                        if attr_name not in item_dict:
                            continue
                        attr_val = item_dict[attr_name]
                        if isinstance(attr_val, str):
                            deserialize_ancestor(ancestor_item_type, attr_val)
                        elif isinstance(attr_val, list):  # type: ignore
                            attr_val: List[str]
                            for ancestor_name in attr_val:
                                deserialize_ancestor(ancestor_item_type, ancestor_name)
        self.from_dict(item_dict)

    @property
    def cache(self) -> ItemCache:
        """
        Gettinging the ItemCache oject.

        .. note:: This is a read only property.

        :getter: This is the ItemCache oject.
        """
        return self._cache

    def _clean_dict_cache(self, name: Optional[str]):
        """
        Clearing the Item dict cache.

        :param obj: The object whose modification invalidates the dict cache.
                    Can be Item, Settings or SIGNATURE_CACHE.
        :param name: The name of Item attribute or None.
        """
        if not self.api.settings().cache_enabled:
            return

        if name is not None and self._inmemory:
            attr = getattr(type(self), name[1:])
            if (
                isinstance(attr, (InheritableProperty, InheritableDictProperty))
                and self.COLLECTION_TYPE != Item.COLLECTION_TYPE
                and self.api.get_items(self.COLLECTION_TYPE).get(self.name) is not None
            ):
                # Invalidating "resolved" caches
                for dep_item in self.descendants:
                    dep_item.cache.set_dict_cache(None, True)

        # Invalidating the cache of the object itself.
        self.cache.clean_dict_cache()

    def clean_cache(self, name: Optional[str] = None):
        """
        Clearing the Item cache.

        :param obj: The object whose modification invalidates the dict cache.
                    Can be Item, Settings or SIGNATURE_CACHE.
        :param name: The name of Item attribute or None.
        """
        if self._inmemory:
            self._clean_dict_cache(name)

    @property
    def inmemory(self) -> bool:
        r"""
        If set to ``false``, only the Item name is in memory. The rest of the Item's properties can be retrieved
        either on demand or as a result of the ``load_items`` background task.

        :getter: The inmemory for the item.
        :setter: The new inmemory value for the object. Should only be used by the Cobbler serializers.
        """
        return self._inmemory

    @inmemory.setter
    def inmemory(self, inmemory: bool):
        """
        Setter for the inmemory of the item.

        :param inmemory: The new inmemory value.
        """
        self._inmemory = inmemory
