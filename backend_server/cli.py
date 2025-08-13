def handle_command(command: str, args: list[str]) -> dict:
    # Remove legacy food search commands (dead code)

    # New commands for the LLM bots
    if command == "visual_context":
        # args: expects a file path to image, or a base64 data URL
        if not args:
            return {"error": "visual_context requires an image path or data URL"}
        param = args[0]
        try:
            if param.startswith("data:image"):
                import base64
                header, b64data = param.split(",", 1)
                image_bytes = base64.b64decode(b64data)
                mime = header.split(";")[0].split(":")[1]
            else:
                from .models.visual_context import analyze_visual_context_from_file
                return analyze_visual_context_from_file(param)

            from .models.visual_context import analyze_visual_context_from_bytes
            return analyze_visual_context_from_bytes(image_bytes, mime)
        except Exception as e:
            return {"error": str(e)}

    if command == "dish_determiner":
        # args: JSON string from previous step, optionally an image token
        if not args:
            return {"error": "dish_determiner requires a JSON string input"}
        try:
            import json
            visual_json = json.loads(args[0])
            image_token = args[1] if len(args) > 1 else visual_json.get("_image_token")
            from .models.dish_determiner import determine_dishes_from_visual_json_and_image
            return determine_dishes_from_visual_json_and_image(visual_json, image_token)
        except Exception as e:
            return {"error": str(e)}

    if command == "restaurant_calories":
        # args: [visual_json_str, dish_json_str, image_token?]
        if not args or len(args) < 2:
            return {"error": "restaurant_calories requires visual_json and dish_json as JSON strings"}
        try:
            import json
            visual_json = json.loads(args[0])
            dish_json = json.loads(args[1])
            image_token = args[2] if len(args) > 2 else (visual_json.get("_image_token") or dish_json.get("_image_token"))
            from .models.resturant_calories import restaurant_calories_pipeline
            return restaurant_calories_pipeline(visual_json, dish_json, image_token)
        except Exception as e:
            return {"error": str(e)}

    if command == "home_cooked":
        # args: [dish_json_str, image_token?]
        if not args:
            return {"error": "home_cooked requires dish_json as a JSON string"}
        try:
            import json
            dish_json = json.loads(args[0])
            image_token = args[1] if len(args) > 1 else (dish_json.get("_image_token"))
            from .models.home_cooked_calories import analyze_home_cooked_from_context_and_image
            return analyze_home_cooked_from_context_and_image(dish_json, image_token)
        except Exception as e:
            return {"error": str(e)}

    return {"error": "Unknown command or arguments"}
