import copy
from functools import wraps
from functools import lru_cache

import ldap
import ldap.modlist
from ldap.ldapobject import ReconnectLDAPObject

# Global state is gross. I'm sorry.
ldap_conn = None
user_search_ou = None
committee_search_ou = None
group_search_ou = None
read_only = False


class HousingLDAPError(Exception):
    pass


def ldap_init(ro, ldap_url, bind_dn, bind_pw, user_ou, group_ou, committee_ou):
    global user_search_ou, group_search_ou, committee_search_ou, ldap_conn, read_only
    read_only = ro
    user_search_ou = user_ou
    group_search_ou = group_ou
    committee_search_ou = committee_ou
    ldap_conn = ReconnectLDAPObject(ldap_url)
    ldap_conn.simple_bind_s(bind_dn, bind_pw)


def ldap_init_required(func):
    @wraps(func)
    def wrapped_func(*args, **kwargs):
        if ldap_conn is not None:
            return func(*args, **kwargs)
        raise HousingLDAPError("LDAP uninitialized. Did you forget to call ldap_init?")

    return wrapped_func


@ldap_init_required
def _ldap_get_field(username, field):
    ldap_results = ldap_conn.search_s(user_search_ou, ldap.SCOPE_SUBTREE, "(uid=%s)"
                                      % username)
    if len(ldap_results) != 1:
        raise HousingLDAPError("Wrong number of results found for username %s."
                               % username)
    if field not in ldap_results[0][1]:
        return None
    return ldap_results[0][1][field][0]


@ldap_init_required
def _ldap_set_field(username, field, new_val):
    if read_only:
        print('LDAP modification: setting %s on %s to %s' % (field,
                                                             username,
                                                             new_val))
        return
    ldap_results = ldap_conn.search_s(user_search_ou, ldap.SCOPE_SUBTREE,
                                      "(uid=%s)" % username)
    if len(ldap_results) != 1:
        raise HousingLDAPError("Wrong number of results found for username %s."
                               % username)
    old_result = ldap_results[0][1]
    new_result = copy.deepcopy(ldap_results[0][1])
    if new_val is not None:
        new_result[field] = [str(new_val).encode('ascii')]
    else:
        new_result[field] = [None]
    ldap_mod_list = ldap.modlist.modifyModlist(old_result, new_result)
    userdn = "uid=%s,%s" % (username, user_search_ou)
    ldap_conn.modify_s(userdn, ldap_mod_list)


@ldap_init_required
def _ldap_get_members():
    return ldap_conn.search_s(user_search_ou, ldap.SCOPE_SUBTREE,
                              "objectClass=houseMember")


@ldap_init_required
def _ldap_is_member_of_group(username, group):
    ldap_results = ldap_conn.search_s(group_search_ou, ldap.SCOPE_SUBTREE,
                                      "(cn=%s)" % group)
    if len(ldap_results) != 1:
        raise HousingLDAPError("Wrong number of results found for group %s." %
                               group)
    return "uid=" + username + "," + user_search_ou in \
           [x.decode('ascii') for x in ldap_results[0][1]['member']]


@ldap_init_required
def _ldap_add_member_to_group(username, group):
    if read_only:
        print("LDAP: Adding user %s to group %s" % (username, group))
        return
    if _ldap_is_member_of_group(username, group):
        return
    ldap_results = ldap_conn.search_s(group_search_ou, ldap.SCOPE_SUBTREE,
                                      "(cn=%s)" % group)
    if len(ldap_results) != 1:
        raise HousingLDAPError("Wrong number of results found for group %s." %
                               group)
    old_results = ldap_results[0][1]
    new_results = copy.deepcopy(ldap_results[0][1])
    new_entry = "uid=%s,%s" % (username, user_search_ou)
    new_entry = new_entry.encode('utf-8')
    new_results['member'].append(new_entry)
    ldap_modlist = ldap.modlist.modifyModlist(old_results, new_results)
    groupdn = "cn=%s,%s" % (group, group_search_ou)
    ldap_conn.modify_s(groupdn, ldap_modlist)


def _ldap_remove_member_from_group(username, group):
    if read_only:
        print("LDAP: Removing user %s from group %s" % (username, group))
        return
    if not _ldap_is_member_of_group(username, group):
        return
    ldap_results = ldap_conn.search_s(group_search_ou, ldap.SCOPE_SUBTREE,
                                      "(cn=%s)" % group)
    if len(ldap_results) != 1:
        raise HousingLDAPError("Wrong number of results found for group %s." %
                               group)
    old_results = ldap_results[0][1]
    new_results = copy.deepcopy(ldap_results[0][1])
    new_results['member'] = [i for i in old_results['member'] if
                             i.decode('utf-8') != "uid=%s,%s" % (username, user_search_ou)]
    ldap_modlist = ldap.modlist.modifyModlist(old_results, new_results)
    groupdn = "cn=%s,%s" % (group, group_search_ou)
    ldap_conn.modify_s(groupdn, ldap_modlist)


@ldap_init_required
def _ldap_is_member_of_committee(username, committee):
    ldap_results = ldap_conn.search_s(committee_search_ou, ldap.SCOPE_SUBTREE,
                                      "(cn=%s)" % committee)
    if len(ldap_results) != 1:
        raise HousingLDAPError("Wrong number of results found for committee %s." %
                               committee)
    return "uid=" + username + "," + user_search_ou in \
           [x.decode('ascii') for x in ldap_results[0][1]['head']]


@lru_cache(maxsize=1024)
def ldap_get_housing_points(username):
    return int(_ldap_get_field(username, 'housingPoints'))


def ldap_get_room_number(username):
    roomno = _ldap_get_field(username, 'roomNumber')
    if roomno is None:
        return "N/A"
    return roomno.decode('utf-8')


@lru_cache(maxsize=1024)
def ldap_get_active_members():
    return [x for x in ldap_get_current_students()
            if ldap_is_active(x['uid'][0].decode('utf-8'))]


@lru_cache(maxsize=1024)
def ldap_get_intro_members():
    return [x for x in ldap_get_current_students()
            if ldap_is_intromember(x['uid'][0].decode('utf-8'))]


@lru_cache(maxsize=1024)
def ldap_get_non_alumni_members():
    return [x for x in ldap_get_current_students()
            if ldap_is_alumni(x['uid'][0].decode('utf-8'))]


@lru_cache(maxsize=1024)
def ldap_get_onfloor_members():
    return [x for x in ldap_get_current_students()
            if ldap_is_onfloor(x['uid'][0].decode('utf-8'))]


@lru_cache(maxsize=1024)
def ldap_get_current_students():
    return [x[1]
            for x in _ldap_get_members()[1:]
            if ldap_is_current_student(str(str(x[0]).split(",")[0]).split("=")[1])]


def ldap_is_active(username):
    return _ldap_is_member_of_group(username, 'active')


def ldap_is_alumni(username):
    # When alumni status becomes a group rather than an attribute this will
    # change to use _ldap_is_member_of_group.
    alum_status = _ldap_get_field(username, 'alumni')
    return alum_status is not None and alum_status.decode('utf-8') == '1'


def ldap_is_eboard(username):
    return _ldap_is_member_of_group(username, 'eboard')


def ldap_is_intromember(username):
    return _ldap_is_member_of_group(username, 'intromembers')


def ldap_is_onfloor(username):
    # april 3rd created onfloor group
    # onfloor_status = _ldap_get_field(username, 'onfloor')
    # return onfloor_status != None and onfloor_status.decode('utf-8') == '1'
    return _ldap_is_member_of_group(username, 'onfloor')


def ldap_is_financial_director(username):
    return _ldap_is_member_of_committee(username, 'Financial')


def ldap_is_eval_director(username):
    # TODO FIXME Evaulations -> Evaluations
    return _ldap_is_member_of_committee(username, 'Evaulations')


def ldap_is_current_student(username):
    return _ldap_is_member_of_group(username, 'current_student')


def ldap_set_housingpoints(username, housing_points):
    _ldap_set_field(username, 'housingPoints', housing_points)


def ldap_set_roomnumber(username, room_number):
    _ldap_set_field(username, 'roomNumber', room_number)


def ldap_set_active(username):
    _ldap_add_member_to_group(username, 'active')


def ldap_set_inactive(username):
    _ldap_remove_member_from_group(username, 'active')


@lru_cache(maxsize=1024)
def ldap_get_name(username):
    return _ldap_get_field(username, 'cn').decode('utf-8')
