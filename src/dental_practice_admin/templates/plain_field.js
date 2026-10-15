// json-editor offers a type switcher for a field with no single type; staff see plain fields.
function plainField(key, field) {
  const plain = {title:(key.charAt(0).toUpperCase()+key.slice(1)).replace(/_/g, ' '), ...field};
  if(field.enum && !field.type) plain.type = 'string';
  if(field.type === 'boolean') plain.format = 'checkbox';
  return plain;
}
function plainSchema(schema) {
  return {...schema, title:' ', properties:Object.fromEntries(Object.entries(schema.properties || {}).map(
    ([key, field]) => [key, plainField(key, field)]))};
}
