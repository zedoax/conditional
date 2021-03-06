from functools import lru_cache
from datetime import datetime
from conditional.util.ldap import ldap_get_housing_points
from conditional.util.ldap import ldap_get_room_number
from conditional.util.ldap import ldap_get_name
from conditional.util.ldap import ldap_is_active
from conditional.util.ldap import ldap_get_onfloor_members
from conditional.util.ldap import ldap_is_current_student

from conditional.models.models import CurrentCoops
from conditional.models.models import OnFloorStatusAssigned

@lru_cache(maxsize=1024)
def __get_ofm__():

    # check that everyone in onfloor has onfloorstatus
    onfloors = [uids['uid'][0].decode('utf-8') for uids in ldap_get_onfloor_members()]

    ofm = [
        {
            'uid': m.uid,
            'time': m.onfloor_granted,
            'points': ldap_get_housing_points(m.uid)
        } for m in OnFloorStatusAssigned.query.all()
        if ldap_is_active(m.uid)
        or CurrentCoops.query.filter(
                CurrentCoops.uid == m.uid and CurrentCoops.active
            ).first() is not None]

    # Add everyone who has a discrepancy in LDAP and OnFloorStatusAssigned
    for member in onfloors:
        if OnFloorStatusAssigned.query.filter(OnFloorStatusAssigned.uid == member).first() is None:
            ofsa = OnFloorStatusAssigned(member, datetime.min)
            active = ldap_is_active(ofsa.uid)
            coop = CurrentCoops.query.filter(CurrentCoops.uid == ofsa.uid).first()
            coop = coop != None and coop.active

            if active or coop:
                ofm.append(
                    {
                        'uid': ofsa.uid,
                        'time': ofsa.onfloor_granted,
                        'points': ldap_get_housing_points(ofsa.uid)
                    })

    # sort by housing points then by time in queue
    ofm.sort(key=lambda m: m['time'])
    ofm.sort(key=lambda m: m['points'], reverse=True)

    return ofm

def get_housing_queue():
    ofm = __get_ofm__()

    queue = [m['uid'] for m in ofm if ldap_get_room_number(m['uid']) == "N/A" and ldap_is_current_student(m['uid'])]

    return queue


def get_queue_with_points():
    ofm = __get_ofm__()

    queue = [
        {
            'name': ldap_get_name(m['uid']),
            'points': m['points']
        } for m in ofm if ldap_get_room_number(m['uid']) == "N/A" and ldap_is_current_student(m['uid'])]

    return queue


def get_queue_length():
    return len(get_housing_queue())


def get_queue_position(username):
    try:
        return get_housing_queue().index(username)
    except (IndexError, ValueError):
        return "0"
