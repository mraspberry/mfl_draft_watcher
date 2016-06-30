#!/usr/bin/env python3

import json
import logging
import logging.handlers
import pprint
import fasteners
import os
import requests
import operator
from datetime import date,datetime,timedelta

_HOME = os.path.expanduser('~')
_RUNDIR = os.path.join(_HOME,'.local')
_CACHEDIR = os.path.join(_RUNDIR,'var','db','mfl_draft_watcher')
_LOGDIR = os.path.join(_RUNDIR,'var','log')
_LOG = os.path.join(_LOGDIR,'draft_watcher.log')
_PLAYERS = os.path.join(_CACHEDIR,'players.json')
_FRANCHISES = os.path.join(_CACHEDIR,'franchises.json')
_DRAFTRES = os.path.join(_CACHEDIR,'draftResults.json')
_MFL_ENDPOINT = 'http://football.myfantasyleague.com/{}/export'.format(date.today().year)

def make_dirs(dirname):
    try:
        os.makedirs(dirname)
    except OSError:
        pass

def fetch_players():
    params = {
        'JSON': 1,
        'TYPE': 'players'
    }
    # players ends up being a list of dicts
    players = requests.get(_MFL_ENDPOINT,params=params).json()['players']['player']
    by_id = index_by_id(players)
    return by_id

def index_by_id(listobj):
    results = dict()
    for item in listobj:
        id = item['id']
        results[id] = item
    return results

def old(filename):
   logging.debug('Checking age of %s',filename)
   try:
        mtime = datetime.fromtimestamp(os.path.getmtime(filename))
   except OSError:
        logging.debug('%s not there. Making it old',filename)
        mtime = datetime.min # make it really old
    
    now = datetime.now()
    thres = timedelta(hours=24)
    diff = now - mtime
   logging.debug("Diff is %s",diff)
    return diff > thres

def get_players():
    if old(_PLAYERS):
        logging.info('Updating player cache')
        players = fetch_players()
        with open(_PLAYERS,'wb') as player_fh:
            json.dump(players,player_fh)
    else:
        with open(_PLAYERS,'rb') as player_fh:
            players = json.load(player_fh)
    
    return players

def fetch_teams(leagueid):
    params = {
        'JSON': 1,
        'TYPE': 'league',
        'L': leagueid,
    }
    results = requests.get(_MFL_ENDPOINT,params=params)
    franchises = results.json()['league']['franchises']['franchise']
    return index_by_id(franchises)

def get_teams(leagueid):
    if old(_FRANCHISES):
        logging.debug('Updating franchise cache')
        teams = fetch_teams(leagueid)
        with open(_FRANCHISES,'wb') as franchise_fh:
            json.dump(teams,franchise_fh)
    else:
        with open(_FRANCHISES,'rb') as franchise_fh:
            teams = json.load(franchise_fh)
    return teams

def post_to_gm(msg):
    data = {'bot_id': 'd3a8cb1e03fd53f5ac66116208','text': msg}
    gm_url = 'https://api.groupme.com/v3/bots/post'
    result = requests.post(gm_url,data=data)
    logging.debug('Result of GM post: %i',result.status_code)

def load_prev_draft_info():
    with open(_DRAFTRES,'rb') as draft_fh:
        draft_info = json.load(draft_fh)
    return draft_info

def write_draft_info(info):
    with open(_DRAFTRES,'wb') as draft_fh:
        json.dump(info,draft_fh)

def get_draft_info(leagueid):
    params = {
        'TYPE': 'draftResult',
        'JSON': 1,
        'L': leagueid,
    }
    try:
        prev_info = load_prev_draft_info()
    except IOError:
        prev_info = dict()

    results = requests.get(_MFL_ENDPOINT,params=params)
    new_info = dict()
    getter = operator.itemgetter('franchise','round','pick')
    picks = results.json()['draftResult']['draftUnit']['draftPick']

    # if x['timestamp'] is an empty string the pick hasn't been made yet
    for pick in filter(lambda x: x['timestamp'],picks):
        prevkey = '_'.join(getter(pick))
        if not prevkey in prev_info:
            new_info[prevkey] = [pick]

    # add in our new data
    prev_info.update(new_info)

    return new_info

@fasteners.interprocess_locked('/tmp/.mfl_draft_watcher.lock')
def main():
    leagueid = 52269
    logger = logging.getLogger(None)
    handle = logging.handlers.TimedRotatingFileHandler(_LOG,when='midnight',backupCount=7)
    formatter = logging.Formatter('%(asctime)s | %(levelno)s | %(message)s')
    handle.formatter = formatter
    logger.addHandler(handle)
    logger.setLevel(logging.DEBUG)
    logging.info('Getting players')
    players = get_players()
    logging.info('Getting teams')
    teams = get_teams(leagueid)
    draft_info = get_draft_info(leagueid)
    msglist = list()
    msg_template = 'With the {num} pick in the To Pimp a Dynasty Draft, {team} selects {player} {pos}'
    for draftkey,draftval in draft_info.items():
        # calculate actual pick number instead of round <num> pick <num>
        roundbase = 12 * (int(draftval['round']) - 1)
        picknum = roundbase + int(draftval['pick'])
        logging.debug(pprint.pformat(draftval))
        playerinfo = players[draftval['player']]
        name = playerinfo['name']
        teaminfo = teams[draftval['franchise']]
        team = teaminfo['name']
        position = playerinfo['position']
        msg = msg.format(
            num=picknum,
            team=team,
            player=name,
            pos=position,
        )
        msglist.append(msg)

    message = '\n'.join(msglist)
    post_to_gm(message)

if __name__ == '__main__':
    main()
