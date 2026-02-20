from storage import load_script

p='Waypoints/thais_circle.json'
try:
    s=load_script(p)
    print('Loaded', s.name, 'waypoints=', len(s.waypoints))
except Exception as e:
    print('Load failed', e)
