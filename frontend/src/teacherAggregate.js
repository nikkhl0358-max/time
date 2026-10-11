// v1861: pure selection/sort logic for the paged "Все преподаватели" view.
export function selectAggregateTeachers(teachers, departments, filter, letter = '', query = '') {
  if (!filter) return [];
  const depNames = new Map((departments || []).map((d)=>[String(d.id), String(d.name || '')]));
  const q = String(query || '').trim().toLocaleLowerCase('ru');
  return (teachers || [])
    .filter((t)=>filter === '__all' || (filter === '__none' ? !t.departmentId : String(t.departmentId || '') === filter))
    .filter((t)=>!letter || String(t.name || '').trim().toUpperCase().startsWith(letter))
    .filter((t)=>!q || String(t.name || '').toLocaleLowerCase('ru').includes(q))
    .slice().sort((a,b)=>
      String(depNames.get(String(a.departmentId)) || 'Без ЦК').localeCompare(String(depNames.get(String(b.departmentId)) || 'Без ЦК'),'ru') ||
      String(a.name || '').localeCompare(String(b.name || ''),'ru'));
}
