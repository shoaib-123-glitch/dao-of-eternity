"""Fail closed before credentials are used for any v0.25 gameplay connection."""
from private_playtest import PlaytestRejected


def check_target(config):
    # Keep v0.24's live playtest database outside this candidate's write targets.
    if (not config.database.startswith('dao_playtest_v023_v025_')
            or not config.user.startswith('dao_playtest_v025_')):
        raise PlaytestRejected('CANDIDATE_TARGET_REJECTED')


def authorize(config,environment):
    check_target(config)
    if environment.get('DAO_V025_CANDIDATE')!='explicitly-authorized':
        raise PlaytestRejected('CANDIDATE_AUTHORIZATION_REQUIRED')
