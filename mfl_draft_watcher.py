#!/usr/bin/env python3

import collections
import fasteners
import json
import logging
import logging.handlers
import operator
import os
import pprint
import requests
import sys
from datetime import date,datetime,timedelta

_HOME = os.path.expanduser('~')
_RUNDIR = os.path.join(_HOME,'.local')
_CACHEDIR = os.path.join(_RUNDIR,'var','db','mfl_draft_watcher')
_LOGDIR = os.path.join(_RUNDIR,'var','log')
_LOG = os.path.join(_LOGDIR,'draft_watcher.log')
_PLAYERS = os.path.join(_CACHEDIR,'players.json')
_FRANCHISES = os.path.join(_CACHEDIR,'franchises.json')
_DRAFTRES = os.path.join(_CACHEDIR,'draftResults.json')
_FULL_LEAGUE = os.path.join(_CACHEDIR,'leagueinfo.json')
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
        with open(_PLAYERS,'w') as player_fh:
            json.dump(players,player_fh)
    else:
        with open(_PLAYERS,'r') as player_fh:
            players = json.load(player_fh)
    
    return players

def cache_league_res(results):
    logging.debug('Caching full league results')
    with open(_FULL_LEAGUE,'w',encoding='utf8') as league_fh:
        json.dump(results,league_fh)

def fetch_teams(leagueid):
    params = {
        'JSON': 1,
        'TYPE': 'league',
        'L': leagueid,
    }
    results = requests.get(_MFL_ENDPOINT,params=params)
    json_res = results.json()
    cache_league_res(json_res)
    franchises = json_res['league']['franchises']['franchise']
    return index_by_id(franchises)

def get_teams(leagueid):
    if old(_FRANCHISES):
        logging.debug('Updating franchise cache')
        teams = fetch_teams(leagueid)
        with open(_FRANCHISES,'w',encoding='utf8') as franchise_fh:
            json.dump(teams,franchise_fh)
    else:
        with open(_FRANCHISES,'r',encoding='utf8') as franchise_fh:
            teams = json.load(franchise_fh)
    return teams

def post_to_gm(msg):
    data = {'bot_id': 'd3a8cb1e03fd53f5ac66116208','text': msg}
    gm_url = 'https://api.groupme.com/v3/bots/post'
    result = requests.post(gm_url,data=data)
    logging.debug('Result of GM post: %i',result.status_code)

def load_prev_draft_info():
    with open(_DRAFTRES,'r',encoding='utf8') as draft_fh:
        draft_info = json.load(draft_fh)
    return draft_info

def write_draft_info(info):
    with open(_DRAFTRES,'w',encoding='utf8') as draft_fh:
        json.dump(info,draft_fh)

def get_league_name():
    with open(_FULL_LEAGUE,encoding='utf8') as league_fh:
        results = json.load(league_fh)

    return results['league']['name']

def get_draft_info(leagueid):
    params = {
        'TYPE': 'draftResults',
        'JSON': 1,
        'L': leagueid,
    }
    try:
        prev_info = load_prev_draft_info()
    except IOError:
        prev_info = collections.OrderedDict()

    results = requests.get(_MFL_ENDPOINT,params=params)
    new_info = collections.OrderedDict()
    getter = operator.itemgetter('franchise','round','pick','player')
    picks = results.json()['draftResults']['draftUnit']['draftPick']

    # if x['timestamp'] is an empty string the pick hasn't been made yet
    for pick in filter(lambda x: x['timestamp'],picks):
        prevkey = '_'.join(getter(pick))
        if not prevkey in prev_info:
            logging.debug('%s not a key in prev_info',prevkey)
            new_info[prevkey] = [pick]

    # add in our new data
    prev_info.update(new_info)
    write_draft_info(prev_info)

    return new_info

@fasteners.interprocess_locked('/tmp/.mfl_draft_watcher.lock')
def main():
    make_dirs(_RUNDIR)
    make_dirs(_CACHEDIR)
    make_dirs(_LOGDIR)
    leagueid = 52269
    logger = logging.getLogger(None)
    handle = logging.handlers.TimedRotatingFileHandler(_LOG,when='midnight',backupCount=7)
    formatter = logging.Formatter('%(asctime)s|%(levelname)8s|%(message)s')
    handle.formatter = formatter
    logger.addHandler(handle)
    logger.setLevel(logging.DEBUG)
    logging.info('Getting players')
    try:
        players = get_players()
        logging.info('Getting teams')
        teams = get_teams(leagueid)
        leaguename = get_league_name()
        draft_info = get_draft_info(leagueid)
        if not draft_info:
            logging.info('No new picks made. Exiting')
            # return here so we do the lock cleanup
            return
        msglist = list()
        ##template = 'With the number {num} pick in the {ln} draft, {team} selects {player} {pos}'
        template = '{num}: {player}, {pos}, {team}'
        for draftkey,draftval in draft_info.items():
            # calculate actual pick number instead of round <num> pick <num>
            draftval = draftval[0]
            ##roundbase = 12 * (int(draftval['round']) - 1)
            ##picknum = roundbase + int(draftval['pick'])
            roundbase = int(draftval['round'])
            pick = draftval['pick']
            picknum = '{}.{}'.format(roundbase,pick)
            logging.debug(pprint.pformat(draftval))
            try:
                playerinfo = players[draftval['player']]
            except KeyError:
                playerinfo = { 'name': 'Devy Draft Pick', 'position': 'N/A' }
            name = playerinfo['name']
            teaminfo = teams[draftval['franchise']]
            team = teaminfo['name']
            position = playerinfo['position']
            msg = template.format(
                ##ln=leaguename,
                num=picknum,
                player=name,
                pos=position,
                team=team,
            )
            msglist.append(msg)
            logging.debug('Added "%s" to msglist',msg)
    except Exception:
        logging.exception('Caught unhandled exception')
        raise

    if msglist:
        message = '\n'.join(msglist)
        logging.debug('GM Message: "%s"',repr(message))
        post_to_gm(message)

if __name__ == '__main__':
    main()
    sys.exit()
