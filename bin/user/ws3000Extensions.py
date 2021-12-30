import schemas.wview
import weewx.units

#
# *******************************************************************
#

# Changes to the database schema to take up to 8 sensors into account
# Required at the time of writing (weewx 3.8.2), this could change in the future...

# Note that extraTemp1-3 already exist in the default schema.
# Same for extraHumid1-2.
ws3000Schema = schemas.wview.schema + [('extraTemp4', 'REAL'),
                                       ('extraTemp5', 'REAL'),
                                       ('extraTemp6', 'REAL'),
                                       ('extraTemp7', 'REAL'),
                                       ('extraTemp8', 'REAL'),
                                       ('extraHumid3', 'REAL'),
                                       ('extraHumid4', 'REAL'),
                                       ('extraHumid5', 'REAL'),
                                       ('extraHumid6', 'REAL'),
                                       ('extraHumid7', 'REAL'),
                                       ('extraHumid8', 'REAL')]

# By default, group_temperature and group_percent only include up to
# extraTemp7 and extraHumid7.
weewx.units.obs_group_dict['extraTemp8'] = 'group_temperature'
weewx.units.obs_group_dict['extraHumid8'] = 'group_percent'
