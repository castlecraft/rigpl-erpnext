import frappe
from urllib.parse import unquote

no_cache = 1


def get_context(context):
	"""
	Handles hierarchical product routes.
	- /category/sub-category -> Item Group page
	- /category/sub-category/item-code -> Item page
	"""
	path = unquote(frappe.request.path).strip("/")

	# The route rule sends everything here, so we need to determine
	# if the path points to an Item Group, an Item, or something else.

	# 1. Check if the entire path maps to an Item Group's custom_route
	item_group = frappe.db.get_value(
		"Item Group",
		{"custom_route": path},
		["name", "item_group_name", "description", "image", "custom_route"],
		as_dict=True,
	)
	if item_group:
		return _get_item_group_context(context, item_group)

	# 2. If not a group, it could be an item within a group.
	# The item code would be the last part of the path.
	path_parts = path.split("/")
	item_code = path_parts[-1]
	
	# The path to the group would be everything except the last part.
	item_group_path = "/".join(path_parts[:-1])

	if item_group_path:
		parent_item_group = frappe.db.get_value(
			"Item Group",
			{"custom_route": item_group_path},
			["name"],
			as_dict=True,
		)
		if parent_item_group:
			# We have a parent group, now let's see if the item exists within it
			item = frappe.db.get_value("Item", {"item_code": item_code, "item_group": parent_item_group.name}, "name")
			if item:
				item_doc = frappe.get_doc("Item", item)
				return _get_item_context(context, item_doc, path)

	# 3. Fallback: Check if the path (or last part of it) is a direct match for an item code.
	# This handles items that might not be in a routed group.
	try:
		# First try full path as item code, for very custom/short URLs
		item_doc = frappe.get_doc("Item", path)
		return _get_item_context(context, item_doc, path)
	except frappe.DoesNotExistError:
		try:
			# Then try last part of the path as item code
			item_doc = frappe.get_doc("Item", item_code)
			return _get_item_context(context, item_doc, path)
		except frappe.DoesNotExistError:
			pass # Not an item, will lead to 404.

	# 4. Nothing matched, so raise a 404
	raise frappe.PageNotFoundError(f"Product or Category not found: {path}")


def _get_item_group_context(context, item_group):
	"""Return context for item group listing page"""
	# Get child items
	child_items = frappe.get_all(
		'Item',
		filters={
			'item_group': item_group['name'],
		},
		fields=['name', 'item_name', 'item_code', 'image', 'description'],
		limit_page_length=999
	)
	
	# Build breadcrumbs
	breadcrumbs = [{'label': 'Home', 'url': '/'}]
	try:
		ig_doc = frappe.get_doc('Item Group', item_group['name'])
		parents = []
		current = ig_doc
		
		# Get parent chain
		while current.parent_item_group:
			try:
				parent = frappe.get_doc('Item Group', current.parent_item_group)
				parents.insert(0, parent)
				current = parent
			except:
				break
		
		# Add all parents to breadcrumbs
		for parent in parents:
			custom_route = frappe.db.get_value('Item Group', parent.name, 'custom_route')
			breadcrumbs.append({
				'label': parent.item_group_name,
				'url': f"/{custom_route}" if custom_route else f"/{parent.name}"
			})
		
		# Add current item group
		breadcrumbs.append({
			'label': item_group['item_group_name'],
			'url': f"/{item_group['custom_route']}"
		})
	except Exception as e:
		frappe.log_error(f"Error building breadcrumbs: {e}")
	
	# Update context with product data
	context.update({
		'item_group': item_group,
		'item_group_name': item_group.get('item_group_name', ''),
		'description': item_group.get('description', ''),
		'image': item_group.get('image', ''),
		'items': child_items,
		'title': item_group.get('item_group_name', ''),
		'breadcrumbs': breadcrumbs,
		'template': 'rigpl_erpnext/templates/product_page.html',
	})
	
	return context


def _get_item_context(context, item, path):
	"""Return context for individual item page"""
	breadcrumbs = [{'label': 'Home', 'url': '/'}]
	
	# Add item group breadcrumbs
	try:
		if item.item_group:
			ig_doc = frappe.get_doc('Item Group', item.item_group)
			parents = []
			current = ig_doc
			
			# Get parent chain
			while current.parent_item_group:
				try:
					parent = frappe.get_doc('Item Group', current.parent_item_group)
					parents.insert(0, parent)
					current = parent
				except:
					break
			
			# Add all parents to breadcrumbs
			for parent in parents:
				custom_route = frappe.db.get_value('Item Group', parent.name, 'custom_route')
				breadcrumbs.append({
					'label': parent.item_group_name,
					'url': f"/{custom_route}" if custom_route else f"/{parent.name}"
				})
			
			# Add current item group
			custom_route = frappe.db.get_value('Item Group', ig_doc.name, 'custom_route')
			breadcrumbs.append({
				'label': ig_doc.item_group_name,
				'url': f"/{custom_route}" if custom_route else f"/{ig_doc.name}"
			})
	except:
		pass
	
	# Add current item
	breadcrumbs.append({
		'label': item.item_name,
		'url': f"/{path}"
	})
	
	context.update({
		'item': item,
		'title': item.item_name,
		'breadcrumbs': breadcrumbs,
		'template': 'rigpl_erpnext/templates/item.html',
	})
	
	return context
